from __future__ import annotations

import json
import math
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import DATA_DIR


STORE_DIR = DATA_DIR / "market_store"
RECORDS_DIR = STORE_DIR / "records"
SOURCE_HEALTH_PATH = STORE_DIR / "source_health.json"
MAX_RECORDS_PER_SYMBOL = 24

_LOCK = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_symbol(value: str) -> str:
    cleaned = "".join(character for character in value.upper() if character.isalnum() or character in "-_^.")
    return cleaned or "UNKNOWN"


def record_market_pull(record: dict) -> dict:
    """Persist a bounded, per-symbol audit trail and update source health."""
    normalized = normalize_json(record)
    normalized.setdefault("record_id", f"market-{uuid.uuid4().hex[:12]}")
    normalized.setdefault("pull_timestamp", utc_now())
    normalized.setdefault("data_type", "daily_price")
    normalized.setdefault("refresh_frequency_seconds", None)
    normalized.setdefault("confidence", {"level": "Low", "reason": "Not validated."})
    normalized.setdefault("last_successful_pull", None)
    normalized.setdefault("error_log", [])
    normalized.setdefault("fallback_source", None)
    normalized.setdefault("raw_source_response", {})
    normalized.setdefault("cleaned_data", {})

    ticker = safe_symbol(str(normalized.get("ticker") or normalized.get("series_id") or "UNKNOWN"))
    path = RECORDS_DIR / f"{ticker}.json"
    with _LOCK:
        payload = read_json(path, {"version": 1, "ticker": ticker, "records": []})
        records = payload.setdefault("records", [])
        records.append(normalized)
        payload["records"] = records[-MAX_RECORDS_PER_SYMBOL:]
        payload["latest_record_id"] = normalized["record_id"]
        payload["updated_at"] = normalized["pull_timestamp"]
        atomic_write_json(path, payload)

        sources = normalized.get("sources") or {}
        fallback_source = normalized.get("fallback_source")
        for source_name, result in sources.items():
            if result.get("cache_hit"):
                continue
            update_source_health(
                source_name,
                success=bool(result.get("success")),
                error=result.get("error"),
                fallback_used=bool(fallback_source == source_name),
                timestamp=normalized["pull_timestamp"],
            )
    return normalized


def latest_market_record(symbol: str) -> dict | None:
    path = RECORDS_DIR / f"{safe_symbol(symbol)}.json"
    payload = read_json(path, {})
    records = payload.get("records") or []
    return records[-1] if records else None


def latest_source_payload(
    symbol: str,
    source: str,
    *,
    successful_only: bool = True,
) -> dict | None:
    path = RECORDS_DIR / f"{safe_symbol(symbol)}.json"
    records = (read_json(path, {}).get("records") or [])[::-1]
    for record in records:
        result = (record.get("sources") or {}).get(source)
        if result and (result.get("success") or not successful_only):
            return result
    return None


def source_health_status(subsystem_states: dict[str, dict] | None = None) -> dict:
    with _LOCK:
        payload = read_json(SOURCE_HEALTH_PATH, default_source_health())
    sources = payload.setdefault("sources", {})
    for source_name in ("yfinance", "stooq", "sec_edgar", "fred", "cfpb"):
        sources.setdefault(source_name, default_source_entry(source_name))
    for source_name, subsystem_state in (subsystem_states or {}).items():
        if source_name not in {"sec_edgar", "fred"} or not isinstance(subsystem_state, dict):
            continue
        sources[source_name] = reconcile_subsystem_health(
            source_name,
            sources.get(source_name) or default_source_entry(source_name),
            subsystem_state,
        )
    active_count = sum(1 for source in sources.values() if source.get("status") == "active")
    configured_count = len(sources)
    overall = "healthy" if active_count == configured_count else "degraded" if active_count else "unavailable"
    update_times = [payload.get("updated_at")]
    update_times.extend(source.get("last_successful_pull") for source in sources.values())
    return {
        "ok": active_count > 0,
        "overall": overall,
        "updated_at": max((value for value in update_times if value), default=None),
        "sources": sources,
    }


def reconcile_subsystem_health(source_name: str, persisted: dict, subsystem: dict) -> dict:
    """Overlay authoritative SEC/FRED cache state without changing their refresh cadence."""
    entry = dict(persisted)
    entry["enabled"] = bool(subsystem.get("enabled", True))
    if not entry["enabled"]:
        entry["status"] = "disabled"
        return entry

    if source_name == "fred":
        configured = bool(subsystem.get("configured"))
        cached_count = int(subsystem.get("series_cached") or 0)
        configured_count = int(subsystem.get("series_configured") or 0)
        last_success = subsystem.get("last_refresh")
        result = subsystem.get("last_result") or {}
        checked = int(result.get("checked") or configured_count or cached_count)
        successful = int(result.get("updated") or (cached_count if cached_count else 0))
    else:
        configured = True
        cached_count = int(subsystem.get("cached_symbol_count") or 0)
        configured_count = int(subsystem.get("mapping_entries") or 0)
        last_success = subsystem.get("last_metadata_check")
        result = subsystem.get("last_result") or {}
        checked = int(result.get("checked") or cached_count)
        errors = result.get("errors") or []
        successful = max(0, checked - len(errors)) if checked else cached_count

    errors = result.get("errors") or []
    ready = configured and cached_count > 0 and bool(last_success or result)
    if not configured:
        entry["status"] = "unavailable"
        entry["last_error"] = f"{source_name} is not configured."
    elif ready and errors:
        entry["status"] = "degraded"
        entry["last_failed_pull"] = last_success or entry.get("last_failed_pull")
        entry["last_error"] = str(errors[0])[:300]
    elif ready:
        entry["status"] = "active"
        entry["last_error"] = None
    elif errors:
        entry["status"] = "unavailable" if not entry.get("last_successful_pull") else "degraded"
        entry["last_failed_pull"] = last_success or entry.get("last_failed_pull")
        entry["last_error"] = str(errors[0])[:300]
    else:
        entry["status"] = "unknown"

    if ready:
        entry["last_successful_pull"] = last_success or entry.get("last_successful_pull")
    if int(entry.get("attempts", 0)) == 0 and (checked or cached_count):
        attempts = max(checked, cached_count, 1)
        failures = min(len(errors), attempts)
        entry["attempts"] = attempts
        entry["successes"] = min(max(successful, attempts - failures), attempts)
        entry["failures"] = failures
        entry["error_rate"] = round(failures / attempts, 4)
    entry["cached_items"] = cached_count
    entry["subsystem_last_result"] = result or None
    return entry


def update_source_health(
    source_name: str,
    *,
    success: bool,
    error: str | None,
    fallback_used: bool,
    timestamp: str | None = None,
) -> None:
    timestamp = timestamp or utc_now()
    with _LOCK:
        payload = read_json(SOURCE_HEALTH_PATH, default_source_health())
        entry = payload.setdefault("sources", {}).setdefault(
            source_name,
            default_source_entry(source_name),
        )
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        if success:
            entry["successes"] = int(entry.get("successes", 0)) + 1
            entry["last_successful_pull"] = timestamp
            entry["status"] = "active"
            entry["last_error"] = None
        else:
            entry["failures"] = int(entry.get("failures", 0)) + 1
            entry["last_failed_pull"] = timestamp
            entry["status"] = "degraded" if entry.get("last_successful_pull") else "unavailable"
            entry["last_error"] = str(error or "Source request failed.")[:300]
        if fallback_used:
            entry["fallback_uses"] = int(entry.get("fallback_uses", 0)) + 1
        attempts = max(1, int(entry.get("attempts", 0)))
        entry["error_rate"] = round(int(entry.get("failures", 0)) / attempts, 4)
        entry["enabled"] = True
        payload["updated_at"] = timestamp
        atomic_write_json(SOURCE_HEALTH_PATH, payload)


def default_source_health() -> dict:
    return {
        "version": 1,
        "updated_at": None,
        "sources": {
            "yfinance": default_source_entry("yfinance"),
            "stooq": default_source_entry("stooq"),
            "sec_edgar": default_source_entry("sec_edgar"),
            "fred": default_source_entry("fred"),
            "cfpb": default_source_entry("cfpb"),
        },
    }


def default_source_entry(name: str) -> dict:
    return {
        "name": name,
        "enabled": True,
        "status": "unknown",
        "attempts": 0,
        "successes": 0,
        "failures": 0,
        "error_rate": 0.0,
        "fallback_uses": 0,
        "last_successful_pull": None,
        "last_failed_pull": None,
        "last_error": None,
    }


def read_json(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return json.loads(json.dumps(fallback))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else json.loads(json.dumps(fallback))
    except (OSError, json.JSONDecodeError):
        return json.loads(json.dumps(fallback))


def atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(normalize_json(value), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def normalize_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): normalize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [normalize_json(item) for item in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    if hasattr(value, "item"):
        try:
            return normalize_json(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)
