from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from config import AGENT_DIR, DATA_DIR
from market_validation import record_unvalidated_yfinance, yfinance_payload_from_bars


DEFAULT_CONSTITUENTS_PATH = DATA_DIR / "sp500.json"
DEFAULT_SNAPSHOT_PATH = DATA_DIR / "sp500_snapshot.json"


def resolve_agent_path(value: str | None, fallback: Path) -> Path:
    if not value:
        return fallback
    path = Path(value)
    return path if path.is_absolute() else AGENT_DIR / path


def normalize_symbol(value: str) -> str:
    return value.strip().upper().replace(".", "-")


def load_constituents(path: Path = DEFAULT_CONSTITUENTS_PATH) -> list[dict]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    source = raw.get("constituents", []) if isinstance(raw, dict) else raw
    constituents: list[dict] = []
    seen: set[str] = set()
    for item in source:
        if isinstance(item, str):
            symbol = normalize_symbol(item)
            name = None
        elif isinstance(item, dict):
            symbol = normalize_symbol(str(item.get("symbol", "")))
            name = item.get("name")
        else:
            continue
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        constituents.append({"symbol": symbol, "name": name})
    return constituents


def load_snapshot(path: Path = DEFAULT_SNAPSHOT_PATH) -> dict:
    if not path.exists():
        return default_snapshot()
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_snapshot()
    defaults = default_snapshot()
    for key, value in defaults.items():
        snapshot.setdefault(key, value)
    return snapshot


def write_snapshot(snapshot: dict, path: Path = DEFAULT_SNAPSHOT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def collect_quote_batch(symbols: list[str], refresh_frequency_seconds: int = 600) -> dict:
    import yfinance as yf

    sampled_at = datetime.now(timezone.utc).isoformat()
    if not symbols:
        return {"sampled_at": sampled_at, "symbols": {}, "errors": []}
    frame = yf.download(
        tickers=symbols,
        period="5d",
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
            payload = yfinance_payload_from_bars(symbol, bars)
            latest = float(closes.iloc[-1])
            previous = float(closes.iloc[-2])
            change = ((latest - previous) / previous) * 100 if previous else 0.0
            results[symbol] = {
                "price": round(latest, 2),
                "change_percent": round(change, 2),
                "sampled_at": sampled_at,
                "source": "yfinance heartbeat cache",
                "stale": False,
                "error": None,
            }
            results[symbol]["confidence"] = record_unvalidated_yfinance(
                symbol,
                payload,
                refresh_frequency_seconds=refresh_frequency_seconds,
            )
        except Exception as exc:
            errors.append(symbol)
            record_unvalidated_yfinance(
                symbol,
                yfinance_payload_from_bars(symbol, [], f"{type(exc).__name__}: {exc}"),
                refresh_frequency_seconds=refresh_frequency_seconds,
            )
    return {"sampled_at": sampled_at, "symbols": results, "errors": errors}


def merge_quote_batch(
    snapshot: dict,
    batch: dict,
    constituent_names: dict[str, str | None],
) -> dict:
    symbols = snapshot.setdefault("symbols", {})
    for symbol, quote in batch.get("symbols", {}).items():
        symbols[symbol] = {
            "symbol": symbol,
            "name": constituent_names.get(symbol),
            **quote,
        }
    for symbol in batch.get("errors", []):
        previous = symbols.get(symbol)
        if previous:
            previous["stale"] = True
            previous["error"] = "Latest batch unavailable; retaining last-known value."
        else:
            symbols[symbol] = {
                "symbol": symbol,
                "name": constituent_names.get(symbol),
                "price": None,
                "change_percent": None,
                "sampled_at": None,
                "source": "yfinance heartbeat cache",
                "stale": True,
                "error": "No cached quote is available.",
            }
    snapshot["last_batch_at"] = batch.get("sampled_at")
    snapshot["updated_at"] = datetime.now(timezone.utc).isoformat()
    return snapshot


def build_ticker_window(sp500_config: dict, watchlist: list[str], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    constituents_path = resolve_agent_path(
        sp500_config.get("constituents_file"),
        DEFAULT_CONSTITUENTS_PATH,
    )
    snapshot_path = resolve_agent_path(
        sp500_config.get("snapshot_file"),
        DEFAULT_SNAPSHOT_PATH,
    )
    constituents = load_constituents(constituents_path)
    watchset = {normalize_symbol(symbol) for symbol in watchlist}
    broader = [item for item in constituents if item["symbol"] not in watchset]
    window_size = max(1, min(40, int(sp500_config.get("ticker_window_size", 28))))
    window_seconds = max(20, int(sp500_config.get("ticker_window_seconds", 38)))
    offset = 0
    if broader:
        cycle = int(now.timestamp()) // window_seconds
        offset = (cycle * window_size) % len(broader)
    selected = circular_slice(broader, offset, window_size)
    snapshot = load_snapshot(snapshot_path)
    max_stale = max(60, int(sp500_config.get("max_stale_seconds", 1800)))
    symbols = [
        quote_for_ticker(item, snapshot.get("symbols", {}), now, max_stale)
        for item in selected
    ]
    return {
        "symbols": symbols,
        "offset": offset,
        "window_size": window_size,
        "window_seconds": window_seconds,
        "constituent_count": len(constituents),
        "cache_updated_at": snapshot.get("updated_at"),
        "cache_completed_at": snapshot.get("completed_at"),
    }


def cached_market_context(symbol: str, sp500_config: dict) -> dict | None:
    normalized = normalize_symbol(symbol)
    constituents_path = resolve_agent_path(
        sp500_config.get("constituents_file"),
        DEFAULT_CONSTITUENTS_PATH,
    )
    constituents = load_constituents(constituents_path)
    metadata = {item["symbol"]: item for item in constituents}
    if normalized not in metadata:
        return None
    snapshot_path = resolve_agent_path(
        sp500_config.get("snapshot_file"),
        DEFAULT_SNAPSHOT_PATH,
    )
    snapshot = load_snapshot(snapshot_path)
    quote = quote_for_ticker(
        metadata[normalized],
        snapshot.get("symbols", {}),
        datetime.now(timezone.utc),
        max(60, int(sp500_config.get("max_stale_seconds", 1800))),
    )
    if not quote["available"]:
        return {
            "found": False,
            "symbol": normalized,
            "name": metadata[normalized].get("name"),
            "error": "No cached S&P 500 quote is available yet.",
            "cached": True,
            "stale": True,
            "data_source": "yfinance heartbeat cache",
            "sampled_at": quote.get("sampled_at"),
        }
    return {
        "found": True,
        "symbol": normalized,
        "name": metadata[normalized].get("name"),
        "price": quote["price"],
        "change_percent": quote["change_percent"],
        "cached": True,
        "stale": quote["stale"],
        "sampled_at": quote["sampled_at"],
        "data_source": "yfinance heartbeat cache",
        "disclaimer": "Preliminary market context, not financial advice.",
    }


def resolve_constituent_name(query: str, sp500_config: dict) -> str | None:
    lowered = query.strip().lower()
    if not lowered:
        return None
    path = resolve_agent_path(sp500_config.get("constituents_file"), DEFAULT_CONSTITUENTS_PATH)
    exact: dict[str, str] = {}
    for item in load_constituents(path):
        name = (item.get("name") or "").lower()
        if name:
            exact[name] = item["symbol"]
    if lowered in exact:
        return exact[lowered]
    matches = [symbol for name, symbol in exact.items() if lowered in name or name in lowered]
    return matches[0] if len(set(matches)) == 1 else None


def quote_for_ticker(item: dict, quotes: dict, now: datetime, max_stale: int) -> dict:
    symbol = item["symbol"]
    quote = quotes.get(symbol) or {}
    sampled_at = parse_datetime(quote.get("sampled_at"))
    too_old = sampled_at is None or (now - sampled_at).total_seconds() > max_stale
    return {
        "symbol": symbol,
        "name": item.get("name") or quote.get("name"),
        "available": quote.get("price") is not None and quote.get("change_percent") is not None,
        "price": quote.get("price"),
        "change_percent": quote.get("change_percent"),
        "sampled_at": quote.get("sampled_at"),
        "source": quote.get("source") or "yfinance heartbeat cache",
        "stale": bool(quote.get("stale") or too_old),
        "pinned": False,
    }


def circular_slice(items: list[dict], offset: int, size: int) -> list[dict]:
    if not items:
        return []
    return [items[(offset + index) % len(items)] for index in range(min(size, len(items)))]


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


def frame_bars(frame, symbol: str, symbol_count: int) -> list[dict]:
    closes = field_series(frame, symbol, symbol_count, "Close")
    adjusted = field_series(frame, symbol, symbol_count, "Adj Close", optional=True)
    volumes = field_series(frame, symbol, symbol_count, "Volume", optional=True)
    bars = []
    for index, close in closes.dropna().items():
        bars.append(
            {
                "date": index.date().isoformat(),
                "close": float(close),
                "adjusted_close": series_value(adjusted, index),
                "volume": int(series_value(volumes, index)) if series_value(volumes, index) is not None else None,
            }
        )
    return bars


def field_series(frame, symbol: str, symbol_count: int, field: str, optional: bool = False):
    try:
        columns = frame.columns
        if getattr(columns, "nlevels", 1) > 1:
            first_level = set(columns.get_level_values(0))
            second_level = set(columns.get_level_values(1))
            if symbol in first_level and field in frame[symbol]:
                return frame[symbol][field]
            if symbol in second_level and field in first_level:
                return frame[field][symbol]
        if symbol_count == 1 and field in frame:
            return frame[field]
    except (KeyError, TypeError):
        pass
    if optional:
        return None
    raise KeyError(f"No {field} series for {symbol}")


def series_value(series, index):
    if series is None:
        return None
    try:
        value = series.loc[index]
        number = float(value)
        return number if number == number else None
    except (KeyError, TypeError, ValueError):
        return None


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def default_snapshot() -> dict:
    return {
        "version": 1,
        "updated_at": None,
        "completed_at": None,
        "last_batch_at": None,
        "symbols": {},
    }
