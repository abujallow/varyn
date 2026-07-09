from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path

from audit import AuditLogger, get_audit_logger
from config import AGENT_DIR, DATA_DIR
from sp500_cache import (
    build_ticker_window,
    collect_quote_batch,
    frame_bars,
    load_constituents,
    load_snapshot,
    merge_quote_batch,
    resolve_agent_path,
    write_snapshot,
)
from market_data_store import source_health_status
from fred import fred_status, load_fred_config, refresh_if_due as refresh_fred_if_due
from sec_edgar import load_sec_config, refresh_watchlist_if_due, sec_status
from market_validation import (
    record_unvalidated_yfinance,
    validate_price_sources,
    yfinance_payload_from_bars,
)
from tools.risk import score_from_context
from safety import SafetyRails, get_safety_rails


CONFIG_PATH = AGENT_DIR / "varyn.config.json"
STATE_PATH = DATA_DIR / "heartbeat_state.json"


class HeartbeatService:
    """Persistent, quiet-by-default market watcher independent of request handling."""

    def __init__(
        self,
        config_path: Path = CONFIG_PATH,
        state_path: Path = STATE_PATH,
        safety: SafetyRails | None = None,
        audit: AuditLogger | None = None,
    ) -> None:
        self.config_path = config_path
        self.state_path = state_path
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="varyn-heartbeat-check")
        self._future: Future | None = None
        self._future_kind: str | None = None
        self._future_meta: dict = {}
        self._future_started = 0.0
        self._timeout_recorded = False
        self._suppress_future = False
        self.safety = safety or get_safety_rails()
        self.audit = audit or get_audit_logger()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self._write_state(default_state())

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._prepare_schedule()
            self._thread = threading.Thread(
                target=self._loop,
                name="varyn-heartbeat",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def status(self) -> dict:
        with self._lock:
            config = self._load_config()
            state = self._read_state()
            now = datetime.now(timezone.utc)
            visible_notices = [
                notice
                for notice in state["notices"]
                if not notice.get("acknowledged_at")
                and parse_datetime(notice.get("visible_at"), now) <= now
            ]
            held_count = sum(
                1
                for notice in state["notices"]
                if not notice.get("acknowledged_at")
                and parse_datetime(notice.get("visible_at"), now) > now
            )
            sorted_notices = sorted(
                visible_notices,
                key=lambda notice: notice.get("created_at", ""),
                reverse=True,
            )
            last_values = state.get("last_values", {})
            market_symbols = []
            for symbol in dict.fromkeys(config["watchlist"]):
                values = last_values.get(symbol) or {}
                market_symbols.append(
                    {
                        "symbol": symbol,
                        "available": bool(values),
                        "price": values.get("price"),
                        "change_percent": values.get("intraday_move_percent"),
                        "source": values.get("source"),
                        "sampled_at": state.get("last_snapshot_at") or state.get("last_run"),
                        "stale": not bool(values),
                        "pinned": True,
                    }
                )
            index_window = build_ticker_window(config["sp500"], config["watchlist"], now)
            seen_symbols = {item["symbol"] for item in market_symbols}
            market_symbols.extend(
                item for item in index_window["symbols"] if item["symbol"] not in seen_symbols
            )
            sp500_state = state.get("sp500", default_sp500_state())
            sec_state = sec_status(config["sec_edgar"])
            fred_state = fred_status(config["fred"])
            return {
                "ok": True,
                "enabled": bool(config["enabled"]),
                "running": bool(
                    not self.safety.proactive_paused()
                    and self._future
                    and not self._future.done()
                ),
                "proactive_paused": self.safety.proactive_paused(),
                "watchlist": config["watchlist"],
                "interval_seconds": config["interval_seconds"],
                "quiet_hours": config["quiet_hours"],
                "next_due": state.get("next_due"),
                "last_run": state.get("last_run"),
                "last_result": state.get("last_result"),
                "notices": sorted_notices,
                "market_snapshot": {
                    "sampled_at": state.get("last_snapshot_at") or state.get("last_run"),
                    "symbols": market_symbols,
                    "watchlist_count": len(config["watchlist"]),
                    "index_window": {
                        key: value for key, value in index_window.items() if key != "symbols"
                    },
                },
                "sp500": {
                    "enabled": bool(config["sp500"]["enabled"]),
                    "task": self._future_kind,
                    "cycle_active": bool(sp500_state.get("cycle_active")),
                    "cursor": int(sp500_state.get("cursor", 0)),
                    "constituent_count": index_window["constituent_count"],
                    "next_batch_due": sp500_state.get("next_batch_due"),
                    "next_refresh_due": sp500_state.get("next_refresh_due"),
                    "last_completed": sp500_state.get("last_completed"),
                    "last_batch_result": sp500_state.get("last_batch_result"),
                },
                "held_notice_count": held_count,
                "recent_history": state["history"][-12:],
                "source_health": source_health_status(
                    {"sec_edgar": sec_state, "fred": fred_state}
                ),
                "sec_edgar": sec_state,
                "fred": fred_state,
            }

    def trigger(self) -> dict:
        with self._lock:
            if self.safety.proactive_paused():
                return {
                    "started": False,
                    "reason": "proactive_paused",
                    **self.status(),
                }
            config = self._load_config()
            if self._future and not self._future.done():
                state = self._read_state()
                self._append_history(
                    state,
                    "check_skipped",
                    "Heartbeat check skipped because another run is active.",
                )
                self._write_state(state)
                return {"started": False, "reason": "already_running", **self.status()}
            self._submit_check(config, forced=True)
            return {"started": True, **self.status()}

    def dismiss(self, notice_id: str) -> dict:
        with self._lock:
            state = self._read_state()
            for notice in state["notices"]:
                if notice.get("id") == notice_id:
                    notice["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
                    self._append_history(
                        state,
                        "notice_dismissed",
                        f"Notice dismissed: {notice.get('title', notice_id)}",
                    )
                    self._write_state(state)
                    return {"ok": True, "dismissed": notice_id}
            raise ValueError(f"No heartbeat notice exists with id {notice_id}.")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:
                with self._lock:
                    state = self._read_state()
                    self._append_history(
                        state,
                        "heartbeat_error",
                        f"Heartbeat loop recovered from: {type(exc).__name__}",
                    )
                    self._write_state(state)
            self._stop_event.wait(1)

    def _tick(self) -> None:
        with self._lock:
            config = self._load_config()
            state = self._read_state()
            self._refresh_interval(state, config)

            if self.safety.proactive_paused():
                if self._future:
                    self._suppress_future = True
                    if self._future.done():
                        self._discard_suppressed_future(state, config)
                return

            if self._future:
                if self._future.done():
                    if self._suppress_future:
                        self._discard_suppressed_future(state, config)
                        return
                    if self._future_kind == "sp500":
                        self._finish_sp500_batch(state, config)
                    else:
                        self._finish_check(state, config)
                else:
                    elapsed = time.monotonic() - self._future_started
                    if elapsed > config["check_timeout_seconds"] and not self._timeout_recorded:
                        self._append_history(
                            state,
                            "check_timeout",
                            "Heartbeat check exceeded its timeout; no overlapping run will start.",
                        )
                        self._timeout_recorded = True
                        self._write_state(state)
                return

            if not config["enabled"]:
                return

            next_due = parse_datetime(state.get("next_due"), datetime.now(timezone.utc))
            if datetime.now(timezone.utc) >= next_due:
                self._submit_check(config, forced=False)
                return

            if self._sp500_due(state, config, datetime.now(timezone.utc)):
                self._submit_sp500_batch(state, config)

    def _discard_suppressed_future(self, state: dict, config: dict) -> None:
        self._future = None
        self._future_kind = None
        self._future_meta = {}
        self._suppress_future = False
        self._append_history(
            state,
            "proactive_result_held",
            "A background result was discarded because proactive behavior was paused.",
        )
        trim_state(state, config)
        self._write_state(state)
        self.audit.log(
            "heartbeat_result_held",
            reason="Global proactive kill switch was active.",
        )

    def _prepare_schedule(self) -> None:
        config = self._load_config()
        state = self._read_state()
        if not state.get("next_due"):
            state["next_due"] = (
                datetime.now(timezone.utc)
                + timedelta(seconds=config["initial_delay_seconds"])
            ).isoformat()
        state["schedule_interval_seconds"] = config["interval_seconds"]
        sp500_state = state.setdefault("sp500", default_sp500_state())
        if not sp500_state.get("next_refresh_due"):
            sp500_state["next_refresh_due"] = (
                datetime.now(timezone.utc)
                + timedelta(seconds=config["sp500"]["initial_delay_seconds"])
            ).isoformat()
        self._write_state(state)

    def _refresh_interval(self, state: dict, config: dict) -> None:
        if state.get("schedule_interval_seconds") == config["interval_seconds"]:
            return
        state["schedule_interval_seconds"] = config["interval_seconds"]
        state["next_due"] = (
            datetime.now(timezone.utc)
            + timedelta(seconds=min(config["initial_delay_seconds"], config["interval_seconds"]))
        ).isoformat()
        self._append_history(
            state,
            "schedule_updated",
            f"Heartbeat interval changed to {config['interval_seconds']} seconds.",
        )
        self._write_state(state)

    def _submit_check(self, config: dict, forced: bool) -> None:
        state = self._read_state()
        if self._future and not self._future.done():
            self._append_history(
                state,
                "check_skipped",
                "Heartbeat check skipped because another run is active.",
            )
            self._write_state(state)
            return

        symbols = list(dict.fromkeys(config["watchlist"] + config["market_stress_symbols"]))
        self._future = self._executor.submit(
            collect_market_snapshot,
            symbols,
            config["watchlist"],
            config["data_layer"],
            config["sec_edgar"],
            config["fred"],
        )
        self._future_kind = "watchlist"
        self._future_meta = {"symbols": symbols}
        self._future_started = time.monotonic()
        self._timeout_recorded = False
        state["next_due"] = (
            datetime.now(timezone.utc)
            + timedelta(seconds=config["interval_seconds"])
        ).isoformat()
        state["last_started"] = datetime.now(timezone.utc).isoformat()
        state["last_trigger"] = "manual" if forced else "scheduled"
        self._write_state(state)

    def _finish_check(self, state: dict, config: dict) -> None:
        future = self._future
        self._future = None
        self._future_kind = None
        self._future_meta = {}
        if not future:
            return

        try:
            snapshot = future.result()
            evaluate_snapshot(state, config, snapshot)
            state["last_snapshot_at"] = snapshot.get("sampled_at")
            state["last_result"] = {
                "status": "completed",
                "symbols_checked": len(snapshot["symbols"]),
                "errors": snapshot["errors"],
                "sec_edgar": snapshot.get("sec_edgar"),
                "fred": snapshot.get("fred"),
            }
            self._append_history(
                state,
                "check_completed",
                f"Heartbeat checked {len(snapshot['symbols'])} symbols.",
            )
            sec_result = snapshot.get("sec_edgar") or {}
            if sec_result.get("status") == "completed":
                self._append_history(
                    state,
                    "sec_refresh_completed",
                    f"SEC metadata checked for {sec_result.get('checked', 0)} watchlist companies; "
                    f"{sec_result.get('refreshed', 0)} fundamentals caches refreshed.",
                )
                self.audit.log(
                    "sec_edgar_refresh",
                    reason="Scheduled official-fundamentals refresh ran inside the heartbeat worker.",
                    details={
                        "checked": sec_result.get("checked", 0),
                        "refreshed": sec_result.get("refreshed", 0),
                        "errors": sec_result.get("errors", [])[:10],
                    },
                )
            fred_result = snapshot.get("fred") or {}
            if fred_result.get("status") == "completed":
                self._append_history(
                    state,
                    "fred_refresh_completed",
                    f"FRED macro cache refreshed {fred_result.get('updated', 0)} of "
                    f"{fred_result.get('checked', 0)} configured series.",
                )
                self.audit.log(
                    "fred_refresh",
                    reason="Scheduled macro refresh ran inside the heartbeat worker.",
                    details={
                        "checked": fred_result.get("checked", 0),
                        "updated": fred_result.get("updated", 0),
                        "errors": fred_result.get("errors", [])[:12],
                    },
                )
        except Exception as exc:
            state["last_result"] = {
                "status": "failed",
                "error": f"{type(exc).__name__}: market data unavailable",
            }
            self._append_history(
                state,
                "check_failed",
                f"Heartbeat check failed safely: {type(exc).__name__}",
            )

        state["last_run"] = datetime.now(timezone.utc).isoformat()
        trim_state(state, config)
        self._write_state(state)

    def _sp500_due(self, state: dict, config: dict, now: datetime) -> bool:
        sp500_config = config["sp500"]
        if not sp500_config["enabled"]:
            return False
        sp500_state = state.setdefault("sp500", default_sp500_state())
        due_key = "next_batch_due" if sp500_state.get("cycle_active") else "next_refresh_due"
        return now >= parse_datetime(sp500_state.get(due_key), now)

    def _submit_sp500_batch(self, state: dict, config: dict) -> None:
        sp500_config = config["sp500"]
        constituents_path = resolve_agent_path(
            sp500_config.get("constituents_file"),
            DATA_DIR / "sp500.json",
        )
        constituents = load_constituents(constituents_path)
        sp500_state = state.setdefault("sp500", default_sp500_state())
        if not constituents:
            sp500_state["next_refresh_due"] = (
                datetime.now(timezone.utc)
                + timedelta(seconds=sp500_config["refresh_interval_seconds"])
            ).isoformat()
            self._append_history(
                state,
                "sp500_unavailable",
                "S&P 500 constituent file is empty or unavailable.",
            )
            self._write_state(state)
            return

        if not sp500_state.get("cycle_active"):
            sp500_state.update(
                {
                    "cycle_active": True,
                    "cursor": 0,
                    "cycle_started": datetime.now(timezone.utc).isoformat(),
                    "next_batch_due": datetime.now(timezone.utc).isoformat(),
                }
            )

        cursor = int(sp500_state.get("cursor", 0))
        batch_size = sp500_config["batch_size"]
        batch_items = constituents[cursor:cursor + batch_size]
        if not batch_items:
            sp500_state["cursor"] = 0
            sp500_state["cycle_active"] = False
            sp500_state["next_refresh_due"] = (
                datetime.now(timezone.utc)
                + timedelta(seconds=sp500_config["refresh_interval_seconds"])
            ).isoformat()
            self._write_state(state)
            return

        symbols = [item["symbol"] for item in batch_items]
        self._future = self._executor.submit(
            collect_quote_batch,
            symbols,
            config["data_layer"]["sp500_refresh_frequency_seconds"],
        )
        self._future_kind = "sp500"
        self._future_meta = {
            "cursor": cursor,
            "count": len(symbols),
            "constituent_count": len(constituents),
            "names": {item["symbol"]: item.get("name") for item in batch_items},
        }
        self._future_started = time.monotonic()
        self._timeout_recorded = False
        sp500_state["last_started"] = datetime.now(timezone.utc).isoformat()
        self._write_state(state)

    def _finish_sp500_batch(self, state: dict, config: dict) -> None:
        future = self._future
        meta = self._future_meta
        self._future = None
        self._future_kind = None
        self._future_meta = {}
        if not future:
            return

        sp500_config = config["sp500"]
        sp500_state = state.setdefault("sp500", default_sp500_state())
        snapshot_path = resolve_agent_path(
            sp500_config.get("snapshot_file"),
            DATA_DIR / "sp500_snapshot.json",
        )
        try:
            batch = future.result()
            snapshot = merge_quote_batch(
                load_snapshot(snapshot_path),
                batch,
                meta.get("names", {}),
            )
            next_cursor = int(meta.get("cursor", 0)) + int(meta.get("count", 0))
            total = int(meta.get("constituent_count", 0))
            complete = next_cursor >= total
            if complete:
                completed_at = datetime.now(timezone.utc).isoformat()
                snapshot["completed_at"] = completed_at
                sp500_state.update(
                    {
                        "cycle_active": False,
                        "cursor": 0,
                        "last_completed": completed_at,
                        "next_batch_due": None,
                        "next_refresh_due": (
                            datetime.now(timezone.utc)
                            + timedelta(seconds=sp500_config["refresh_interval_seconds"])
                        ).isoformat(),
                    }
                )
                self._append_history(
                    state,
                    "sp500_refresh_completed",
                    f"S&P 500 cache refreshed across {total} constituents.",
                )
            else:
                sp500_state.update(
                    {
                        "cycle_active": True,
                        "cursor": next_cursor,
                        "next_batch_due": (
                            datetime.now(timezone.utc)
                            + timedelta(seconds=sp500_config["batch_delay_seconds"])
                        ).isoformat(),
                    }
                )
            sp500_state["last_batch_result"] = {
                "status": "completed",
                "requested": int(meta.get("count", 0)),
                "received": len(batch.get("symbols", {})),
                "retained_stale": len(batch.get("errors", [])),
                "sampled_at": batch.get("sampled_at"),
            }
            write_snapshot(snapshot, snapshot_path)
        except Exception as exc:
            sp500_state["last_batch_result"] = {
                "status": "failed",
                "error": f"{type(exc).__name__}: batch unavailable",
            }
            sp500_state["next_batch_due"] = (
                datetime.now(timezone.utc)
                + timedelta(seconds=sp500_config["batch_delay_seconds"])
            ).isoformat()
            self._append_history(
                state,
                "sp500_batch_failed",
                f"S&P 500 batch failed safely: {type(exc).__name__}",
            )

        trim_state(state, config)
        self._write_state(state)

    def _load_config(self) -> dict:
        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        heartbeat = raw.get("heartbeat")
        if not isinstance(heartbeat, dict):
            raise RuntimeError("varyn.config.json must contain a heartbeat object.")

        required = {
            "enabled",
            "interval_seconds",
            "check_timeout_seconds",
            "initial_delay_seconds",
            "watchlist",
            "market_stress_symbols",
            "thresholds",
            "quiet_hours",
            "history_limit",
            "notice_limit",
        }
        missing = required.difference(heartbeat)
        if missing:
            raise RuntimeError(f"Heartbeat config is missing: {', '.join(sorted(missing))}")

        heartbeat["interval_seconds"] = max(2, int(heartbeat["interval_seconds"]))
        heartbeat["check_timeout_seconds"] = max(10, int(heartbeat["check_timeout_seconds"]))
        heartbeat["initial_delay_seconds"] = max(1, int(heartbeat["initial_delay_seconds"]))
        sp500 = raw.get("sp500")
        if not isinstance(sp500, dict):
            raise RuntimeError("varyn.config.json must contain an sp500 object.")
        required_sp500 = {
            "enabled",
            "constituents_file",
            "snapshot_file",
            "refresh_interval_seconds",
            "batch_size",
            "batch_delay_seconds",
            "initial_delay_seconds",
            "ticker_window_size",
            "ticker_window_seconds",
            "max_stale_seconds",
        }
        missing_sp500 = required_sp500.difference(sp500)
        if missing_sp500:
            raise RuntimeError(
                f"S&P 500 config is missing: {', '.join(sorted(missing_sp500))}"
            )
        sp500["refresh_interval_seconds"] = max(300, int(sp500["refresh_interval_seconds"]))
        sp500["batch_size"] = max(10, min(50, int(sp500["batch_size"])))
        sp500["batch_delay_seconds"] = max(3, int(sp500["batch_delay_seconds"]))
        sp500["initial_delay_seconds"] = max(2, int(sp500["initial_delay_seconds"]))
        sp500["ticker_window_size"] = max(10, min(40, int(sp500["ticker_window_size"])))
        sp500["ticker_window_seconds"] = max(20, int(sp500["ticker_window_seconds"]))
        sp500["max_stale_seconds"] = max(300, int(sp500["max_stale_seconds"]))
        heartbeat["sp500"] = sp500
        data_layer = raw.get("data_layer")
        if not isinstance(data_layer, dict):
            raise RuntimeError("varyn.config.json must contain a data_layer object.")
        required_data_layer = {
            "enabled",
            "store_directory",
            "stooq_validation_interval_seconds",
            "agreement_tolerance_percent",
            "minor_difference_percent",
            "watchlist_refresh_frequency_seconds",
            "sp500_refresh_frequency_seconds",
        }
        missing_data_layer = required_data_layer.difference(data_layer)
        if missing_data_layer:
            raise RuntimeError(
                f"Data-layer config is missing: {', '.join(sorted(missing_data_layer))}"
            )
        data_layer["stooq_validation_interval_seconds"] = max(
            300,
            int(data_layer["stooq_validation_interval_seconds"]),
        )
        data_layer["agreement_tolerance_percent"] = max(
            0.05,
            float(data_layer["agreement_tolerance_percent"]),
        )
        data_layer["minor_difference_percent"] = max(
            data_layer["agreement_tolerance_percent"],
            float(data_layer["minor_difference_percent"]),
        )
        data_layer["watchlist_refresh_frequency_seconds"] = max(
            60,
            int(data_layer["watchlist_refresh_frequency_seconds"]),
        )
        data_layer["sp500_refresh_frequency_seconds"] = max(
            300,
            int(data_layer["sp500_refresh_frequency_seconds"]),
        )
        heartbeat["data_layer"] = data_layer
        sec_config = raw.get("sec_edgar")
        if not isinstance(sec_config, dict):
            raise RuntimeError("varyn.config.json must contain a sec_edgar object.")
        heartbeat["sec_edgar"] = load_sec_config(sec_config)
        fred_config = raw.get("fred")
        if not isinstance(fred_config, dict):
            raise RuntimeError("varyn.config.json must contain a fred object.")
        heartbeat["fred"] = load_fred_config(fred_config)
        return heartbeat

    def _read_state(self) -> dict:
        if not self.state_path.exists():
            return default_state()
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = default_state()
        defaults = default_state()
        for key, value in defaults.items():
            state.setdefault(key, value)
        return state

    def _write_state(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.state_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(state, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(self.state_path)

    def _append_history(self, state: dict, event_type: str, message: str) -> None:
        state.setdefault("history", []).append(
            {
                "id": f"event-{uuid.uuid4().hex[:10]}",
                "type": event_type,
                "message": message,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )


def heartbeat_risk_score(symbol: str, market_context: dict) -> int:
    """Lightweight, always-numeric risk score for watchlist alert thresholds.

    Uses score_from_context() directly rather than the user-facing
    build_risk_analysis(), which now refuses a numeric score unless it has
    fundamentals (beta/debt-to-equity/current ratio) that the heartbeat's
    price-only snapshot intentionally never fetches (see Section 66: keep
    expensive-to-poll data out of the proactive loop). The heartbeat only
    ever needs a move-based number for its own threshold comparisons, not a
    fundamentals-grounded memo score, so it isn't subject to that gate.
    """
    scores = score_from_context(f"Assess {symbol} market risk", market_context)
    return round(sum(scores.values()) / len(scores))


def collect_market_snapshot(
    symbols: list[str],
    watchlist: list[str],
    data_layer_config: dict,
    sec_config: dict | None = None,
    fred_config: dict | None = None,
) -> dict:
    import yfinance as yf

    frame = yf.download(
        tickers=symbols,
        period="7d",
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=True,
    )
    results: dict[str, dict] = {}
    errors: list[str] = []

    for symbol in symbols:
        try:
            closes = close_series(frame, symbol, len(symbols)).dropna()
            if len(closes) < 2:
                raise ValueError("fewer than two closing prices")
            bars = frame_bars(frame, symbol, len(symbols))
            yfinance_payload = yfinance_payload_from_bars(symbol, bars)
            latest = float(closes.iloc[-1])
            previous = float(closes.iloc[-2])
            first = float(closes.iloc[max(0, len(closes) - 6)])
            day_move = percent_change(latest, previous)
            five_day_move = percent_change(latest, first)
            market_context = {
                "found": True,
                "symbol": symbol,
                "price": round(latest, 2),
                "previous_close": round(previous, 2),
                "change_percent": round(day_move, 2),
                "data_source": "yfinance",
            }
            results[symbol] = {
                "price": round(latest, 2),
                "intraday_move_percent": round(day_move, 2),
                "five_day_move_percent": round(five_day_move, 2),
                "risk_score": heartbeat_risk_score(symbol, market_context),
                "source": "yfinance",
            }
            if symbol in watchlist:
                validated = validate_price_sources(
                    symbol,
                    yfinance_payload,
                    refresh_frequency_seconds=data_layer_config["watchlist_refresh_frequency_seconds"],
                    stooq_max_age_seconds=data_layer_config["stooq_validation_interval_seconds"],
                    agreement_tolerance_percent=data_layer_config["agreement_tolerance_percent"],
                    minor_difference_percent=data_layer_config["minor_difference_percent"],
                )
                if validated.get("found"):
                    results[symbol].update(
                        {
                            "price": round(float(validated["price"]), 2),
                            "intraday_move_percent": round(float(validated.get("change_percent") or day_move), 2),
                            "source": validated.get("data_source") or "yfinance",
                            "confidence": validated.get("confidence"),
                            "source_changed": validated.get("source_changed", False),
                        }
                    )
            else:
                results[symbol]["confidence"] = record_unvalidated_yfinance(
                    symbol,
                    yfinance_payload,
                    refresh_frequency_seconds=data_layer_config["watchlist_refresh_frequency_seconds"],
                )
        except Exception as exc:
            yfinance_payload = yfinance_payload_from_bars(
                symbol,
                [],
                f"{type(exc).__name__}: {exc}",
            )
            if symbol in watchlist:
                validated = validate_price_sources(
                    symbol,
                    yfinance_payload,
                    refresh_frequency_seconds=data_layer_config["watchlist_refresh_frequency_seconds"],
                    stooq_max_age_seconds=data_layer_config["stooq_validation_interval_seconds"],
                    agreement_tolerance_percent=data_layer_config["agreement_tolerance_percent"],
                    minor_difference_percent=data_layer_config["minor_difference_percent"],
                )
                if validated.get("found"):
                    change = float(validated.get("change_percent") or 0)
                    market_context = {
                        "found": True,
                        "symbol": symbol,
                        "price": validated["price"],
                        "change_percent": change,
                        "data_source": validated.get("data_source"),
                    }
                    results[symbol] = {
                        "price": round(float(validated["price"]), 2),
                        "intraday_move_percent": round(change, 2),
                        "five_day_move_percent": 0.0,
                        "risk_score": heartbeat_risk_score(symbol, market_context),
                        "source": "stooq",
                        "confidence": validated.get("confidence"),
                        "source_changed": True,
                    }
                    continue
            errors.append(f"{symbol}: market data unavailable")

    sec_result = refresh_watchlist_if_due(watchlist, sec_config)
    fred_result = refresh_fred_if_due(fred_config)
    return {
        "sampled_at": datetime.now(timezone.utc).isoformat(),
        "symbols": results,
        "errors": errors,
        "sec_edgar": sec_result,
        "fred": fred_result,
    }


def close_series(frame, symbol: str, symbol_count: int):
    columns = frame.columns
    if getattr(columns, "nlevels", 1) > 1:
        first_level = set(columns.get_level_values(0))
        second_level = set(columns.get_level_values(1))
        if symbol in first_level:
            return frame[symbol]["Close"]
        if symbol in second_level:
            return frame["Close"][symbol]
    if symbol_count == 1 and "Close" in frame:
        return frame["Close"]
    raise KeyError(f"No close series for {symbol}")


def evaluate_snapshot(state: dict, config: dict, snapshot: dict) -> None:
    thresholds = config["thresholds"]
    previous_values = state.get("last_values", {})
    previous_conditions = set(state.get("active_conditions", []))
    current_conditions: set[str] = set()
    now = datetime.now().astimezone()

    for symbol, values in snapshot["symbols"].items():
        if symbol in config["watchlist"]:
            evaluate_watched_symbol(
                state,
                symbol,
                values,
                previous_values.get(symbol),
                thresholds,
                config["quiet_hours"],
                previous_conditions,
                current_conditions,
                now,
            )
        if symbol in config["market_stress_symbols"]:
            evaluate_market_stress(
                state,
                symbol,
                values,
                thresholds,
                config["quiet_hours"],
                previous_conditions,
                current_conditions,
                now,
            )

    state["active_conditions"] = sorted(current_conditions)
    state["last_values"] = snapshot["symbols"]

    for error in snapshot["errors"]:
        state.setdefault("history", []).append(
            {
                "id": f"event-{uuid.uuid4().hex[:10]}",
                "type": "data_unavailable",
                "message": error,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )


def evaluate_watched_symbol(
    state: dict,
    symbol: str,
    values: dict,
    previous: dict | None,
    thresholds: dict,
    quiet_hours: dict,
    previous_conditions: set[str],
    current_conditions: set[str],
    now: datetime,
) -> None:
    day_move = float(values["intraday_move_percent"])
    five_day_move = float(values["five_day_move_percent"])
    risk_score = int(values["risk_score"]) if values.get("risk_score") is not None else None

    if abs(day_move) >= float(thresholds["intraday_move_percent"]):
        direction = "up" if day_move >= 0 else "down"
        key = f"intraday:{symbol}:{direction}"
        current_conditions.add(key)
        critical = abs(day_move) >= float(thresholds["critical_move_percent"])
        add_condition_notice(
            state,
            key,
            previous_conditions,
            "critical" if critical else "warning",
            f"{symbol} material price move",
            f"{symbol} moved {day_move:+.2f}% versus the prior close.",
            symbol,
            quiet_hours,
            now,
            values,
        )

    if abs(five_day_move) >= float(thresholds["five_day_move_percent"]):
        direction = "up" if five_day_move >= 0 else "down"
        key = f"five_day:{symbol}:{direction}"
        current_conditions.add(key)
        critical = abs(five_day_move) >= float(thresholds["critical_move_percent"])
        add_condition_notice(
            state,
            key,
            previous_conditions,
            "critical" if critical else "warning",
            f"{symbol} five-day risk event",
            f"{symbol} moved {five_day_move:+.2f}% across the latest five-session window.",
            symbol,
            quiet_hours,
            now,
            values,
        )

    previous_score = int(previous["risk_score"]) if previous and previous.get("risk_score") is not None else None
    if risk_score is not None and risk_score >= int(thresholds["risk_score"]):
        key = f"risk_score:{symbol}"
        current_conditions.add(key)
        if previous_score is None or previous_score < int(thresholds["risk_score"]):
            add_condition_notice(
                state,
                key,
                previous_conditions,
                "critical" if risk_score >= int(thresholds["critical_risk_score"]) else "warning",
                f"{symbol} risk threshold crossed",
                f"{symbol}'s preliminary Varyn risk score reached {risk_score}.",
                symbol,
                quiet_hours,
                now,
                values,
            )

    if (
        risk_score is not None
        and previous_score is not None
        and previous_score >= int(thresholds["risk_score"])
        and risk_score - previous_score >= int(thresholds["risk_score_increase"])
    ):
        key = f"risk_increase:{symbol}:{risk_score}"
        current_conditions.add(key)
        add_condition_notice(
            state,
            key,
            previous_conditions,
            "critical" if risk_score >= int(thresholds["critical_risk_score"]) else "warning",
            f"{symbol} risk score increased",
            f"{symbol}'s preliminary risk score increased from {previous_score} to {risk_score}.",
            symbol,
            quiet_hours,
            now,
            values,
        )


def evaluate_market_stress(
    state: dict,
    symbol: str,
    values: dict,
    thresholds: dict,
    quiet_hours: dict,
    previous_conditions: set[str],
    current_conditions: set[str],
    now: datetime,
) -> None:
    move = float(values["intraday_move_percent"])
    if abs(move) < float(thresholds["market_stress_move_percent"]):
        return
    direction = "up" if move >= 0 else "down"
    key = f"market_stress:{symbol}:{direction}"
    current_conditions.add(key)
    critical = abs(move) >= float(thresholds["critical_move_percent"])
    add_condition_notice(
        state,
        key,
        previous_conditions,
        "critical" if critical else "warning",
        f"Market stress signal: {symbol}",
        f"{symbol} moved {move:+.2f}% versus the prior close.",
        symbol,
        quiet_hours,
        now,
        values,
    )


def add_condition_notice(
    state: dict,
    fingerprint: str,
    previous_conditions: set[str],
    severity: str,
    title: str,
    message: str,
    symbol: str,
    quiet_hours: dict,
    now: datetime,
    values: dict,
) -> None:
    if fingerprint in previous_conditions:
        return
    visible_at = now if severity == "critical" else next_visible_time(now, quiet_hours)
    confidence = values.get("confidence") or {}
    window = "five sessions" if fingerprint.startswith("five_day:") else "intraday"
    risk_read = build_notice_risk_read(fingerprint, symbol, values)
    notice = {
            "id": f"notice-{uuid.uuid4().hex[:12]}",
            "fingerprint": fingerprint,
            "severity": severity,
            "title": title,
            "message": message,
            "symbol": symbol,
            "created_at": now.astimezone(timezone.utc).isoformat(),
            "visible_at": visible_at.astimezone(timezone.utc).isoformat(),
            "acknowledged_at": None,
            "source": values.get("source") or "Varyn heartbeat / yfinance",
            "confidence": confidence.get("level") or "Unrated",
            "window": window,
            "move_percent": (
                values.get("five_day_move_percent")
                if window == "five sessions"
                else values.get("intraday_move_percent")
            ),
            "risk_score": values.get("risk_score"),
            "risk_read": risk_read,
            "analysis_prompt": f"Analyze {symbol} market, credit, liquidity, and operational risk.",
        }
    state.setdefault("notices", []).append(notice)
    get_audit_logger().log(
        "heartbeat_notice",
        reason="Heartbeat threshold created a user-facing risk notice.",
        details={
            "notice_id": notice["id"],
            "symbol": symbol,
            "severity": severity,
            "window": window,
            "move_percent": notice["move_percent"],
            "risk_score": notice["risk_score"],
            "source": notice["source"],
            "confidence": notice["confidence"],
            "visible_at": notice["visible_at"],
        },
    )


def build_notice_risk_read(fingerprint: str, symbol: str, values: dict) -> str:
    if fingerprint.startswith("five_day:"):
        move = float(values.get("five_day_move_percent") or 0)
        return (
            f"{symbol}'s five-session move is material enough to warrant a broader volatility, "
            "liquidity, and catalyst review."
            if abs(move) >= 8
            else f"{symbol}'s multi-session move merits monitoring for persistence."
        )
    if fingerprint.startswith("risk_score:") or fingerprint.startswith("risk_increase:"):
        return (
            f"{symbol}'s preliminary risk score indicates elevated combined market, credit, "
            "liquidity, or operational exposure."
        )
    return (
        f"{symbol}'s latest move is outside the configured materiality threshold; review the "
        "driver before treating it as a durable trend."
    )


def next_visible_time(now: datetime, quiet_hours: dict) -> datetime:
    start = parse_clock(quiet_hours["start"])
    end = parse_clock(quiet_hours["end"])
    if not is_quiet_time(now.timetz().replace(tzinfo=None), start, end):
        return now
    target = datetime.combine(now.date(), end, tzinfo=now.tzinfo)
    if now.time().replace(tzinfo=None) >= start:
        target += timedelta(days=1)
    return target


def is_quiet_time(current: clock_time, start: clock_time, end: clock_time) -> bool:
    if start < end:
        return start <= current < end
    return current >= start or current < end


def parse_clock(value: str) -> clock_time:
    hour, minute = value.split(":", 1)
    return clock_time(hour=int(hour), minute=int(minute))


def parse_datetime(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return fallback


def percent_change(current: float, previous: float) -> float:
    return ((current - previous) / previous) * 100 if previous else 0.0


def trim_state(state: dict, config: dict) -> None:
    history_limit = max(10, int(config["history_limit"]))
    notice_limit = max(10, int(config["notice_limit"]))
    state["history"] = state.get("history", [])[-history_limit:]
    state["notices"] = state.get("notices", [])[-notice_limit:]


def default_state() -> dict:
    return {
        "version": 1,
        "next_due": None,
        "schedule_interval_seconds": None,
        "last_started": None,
        "last_run": None,
        "last_trigger": None,
        "last_result": None,
        "last_snapshot_at": None,
        "last_values": {},
        "active_conditions": [],
        "notices": [],
        "history": [],
        "sp500": default_sp500_state(),
    }


def default_sp500_state() -> dict:
    return {
        "cycle_active": False,
        "cursor": 0,
        "cycle_started": None,
        "last_started": None,
        "last_completed": None,
        "next_batch_due": None,
        "next_refresh_due": None,
        "last_batch_result": None,
    }
