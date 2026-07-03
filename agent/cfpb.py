from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from config import AGENT_DIR
from market_data_store import update_source_health
from varyn_settings import setting


SOURCE_NAME = "CFPB Consumer Complaint Database"
_LOCK = threading.RLock()
_LAST_REQUEST_AT = 0.0


def cfpb_config() -> dict:
    configured = setting("cfpb", {}) or {}
    cache_path = Path(configured.get("cache_directory", "data/cfpb"))
    return {
        "enabled": bool(configured.get("enabled", True)),
        "base_url": str(
            configured.get(
                "base_url",
                "https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/",
            )
        ).rstrip("/")
        + "/",
        "cache_directory": cache_path if cache_path.is_absolute() else AGENT_DIR / cache_path,
        "cache_ttl_seconds": int(configured.get("cache_ttl_seconds", 21600)),
        "stale_after_seconds": int(configured.get("stale_after_seconds", 172800)),
        "lookback_days": int(configured.get("lookback_days", 90)),
        "request_timeout_seconds": float(configured.get("request_timeout_seconds", 20)),
        "request_interval_seconds": float(configured.get("request_interval_seconds", 0.5)),
        "company_mappings": configured.get("company_mappings") or {},
    }


def get_complaint_signal(symbol: str, *, force: bool = False) -> dict:
    config = cfpb_config()
    clean_symbol = normalize_symbol(symbol)
    mapping = config["company_mappings"].get(clean_symbol) or {}

    if not config["enabled"]:
        return unavailable_signal(clean_symbol, "CFPB integration is disabled in configuration.")
    if not mapping or mapping.get("applicable") is False:
        return non_applicable_signal(
            clean_symbol,
            mapping.get("reason") or "No CFPB complaint-company mapping is configured for this symbol.",
        )

    cache_path = config["cache_directory"] / f"{clean_symbol}.json"
    cached = read_json(cache_path)
    if cached and not force and cache_age_seconds(cached) <= config["cache_ttl_seconds"]:
        return {**cached.get("cleaned_data", {}), "cache_hit": True}

    try:
        signal, raw = fetch_signal(clean_symbol, mapping, config)
        record = {
            "version": 1,
            "ticker": clean_symbol,
            "data_type": "consumer_complaint_trend",
            "source_name": SOURCE_NAME,
            "pull_timestamp": signal["pulled_at"],
            "refresh_frequency_seconds": config["cache_ttl_seconds"],
            "confidence_score": signal["confidence"],
            "last_successful_pull": signal["pulled_at"],
            "error_log": [],
            "fallback_source": None,
            "raw_source_response": raw,
            "cleaned_data": signal,
        }
        write_json(cache_path, record)
        update_source_health(
            "cfpb",
            success=True,
            error=None,
            fallback_used=False,
            timestamp=signal["pulled_at"],
        )
        return signal
    except Exception as exc:
        error = str(exc)[:300]
        update_source_health(
            "cfpb",
            success=False,
            error=error,
            fallback_used=False,
        )
        if cached and cached.get("cleaned_data"):
            retained = dict(cached["cleaned_data"])
            retained["stale"] = True
            retained["cache_hit"] = True
            retained["error"] = error
            retained["confidence"] = {
                "level": "Low",
                "reason": "A retained CFPB aggregate is being used after the latest refresh failed.",
            }
            return retained
        return unavailable_signal(clean_symbol, error, mapping=mapping)


def get_complaint_signals(symbols: list[str], *, force: bool = False) -> list[dict]:
    results = []
    seen = set()
    for symbol in symbols:
        clean = normalize_symbol(symbol)
        if clean in seen:
            continue
        seen.add(clean)
        results.append(get_complaint_signal(clean, force=force))
    return results


def fetch_signal(symbol: str, mapping: dict, config: dict) -> tuple[dict, dict]:
    end = date.today()
    days = max(7, config["lookback_days"])
    current_start = end - timedelta(days=days - 1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)
    company = str(mapping["company"])

    current = fetch_count(company, current_start, end, config)
    previous = fetch_count(company, previous_start, previous_end, config)
    current_count = current["count"]
    previous_count = previous["count"]
    change_count = current_count - previous_count
    change_percent = (
        round((change_count / previous_count) * 100, 2)
        if previous_count > 0
        else None
    )
    meta = current.get("meta") or {}
    flagged = bool(current.get("timed_out") or previous.get("timed_out") or meta.get("has_data_issue"))
    confidence = {
        "level": "Flagged" if flagged else "High",
        "reason": (
            "The official CFPB aggregate reported a timeout or data-quality flag."
            if flagged
            else "Official CFPB complaint aggregates were retrieved successfully for both comparison windows."
        ),
    }
    pulled_at = utc_now()
    signal = {
        "found": True,
        "applicable": True,
        "symbol": symbol,
        "company": company,
        "relationship": mapping.get("relationship") or "mapped company",
        "source": SOURCE_NAME,
        "source_url": config["base_url"],
        "pulled_at": pulled_at,
        "data_through": meta.get("last_updated") or meta.get("last_indexed") or end.isoformat(),
        "current_window": {
            "start": current_start.isoformat(),
            "end": end.isoformat(),
            "count": current_count,
        },
        "previous_window": {
            "start": previous_start.isoformat(),
            "end": previous_end.isoformat(),
            "count": previous_count,
        },
        "change_count": change_count,
        "change_percent": change_percent,
        "direction": trend_direction(change_percent, current_count, previous_count),
        "confidence": confidence,
        "stale": False,
        "cache_hit": False,
        "risk_read": risk_read(company, current_count, previous_count, change_percent),
        "caveat": (
            "Complaint counts are unadjusted for company size and do not establish wrongdoing, "
            "regulatory breach, or complaint validity."
        ),
    }
    raw = {
        "current_window": current,
        "previous_window": previous,
    }
    return signal, raw


def fetch_count(company: str, start: date, end: date, config: dict) -> dict:
    throttle(config["request_interval_seconds"])
    response = requests.get(
        config["base_url"],
        params={
            "company": company,
            "date_received_min": start.isoformat(),
            "date_received_max": end.isoformat(),
            "size": 1,
            "no_aggs": "true",
        },
        headers={"User-Agent": "Varyn Risk Intelligence/0.1 (educational risk research)"},
        timeout=config["request_timeout_seconds"],
    )
    response.raise_for_status()
    payload = response.json()
    total = ((payload.get("hits") or {}).get("total") or 0)
    count = int(total.get("value", 0) if isinstance(total, dict) else total)
    meta = payload.get("_meta") or {}
    return {
        "count": count,
        "took_ms": payload.get("took"),
        "timed_out": bool(payload.get("timed_out")),
        "meta": {
            "last_updated": meta.get("last_updated"),
            "last_indexed": meta.get("last_indexed"),
            "is_data_stale": meta.get("is_data_stale"),
            "has_data_issue": meta.get("has_data_issue"),
        },
    }


def cfpb_status() -> dict:
    config = cfpb_config()
    cache_dir = config["cache_directory"]
    records = [read_json(path) for path in cache_dir.glob("*.json")] if cache_dir.exists() else []
    successful = [record for record in records if record and record.get("last_successful_pull")]
    last_refresh = max(
        (record.get("last_successful_pull") for record in successful),
        default=None,
    )
    return {
        "enabled": config["enabled"],
        "source": SOURCE_NAME,
        "authentication": "keyless",
        "cached_symbol_count": len(successful),
        "last_refresh": last_refresh,
        "refresh_mode": "cached on demand",
        "lookback_days": config["lookback_days"],
        "mapped_symbols": sorted(config["company_mappings"]),
    }


def non_applicable_signal(symbol: str, reason: str) -> dict:
    return {
        "found": True,
        "applicable": False,
        "symbol": symbol,
        "company": None,
        "source": SOURCE_NAME,
        "pulled_at": None,
        "data_through": None,
        "current_window": None,
        "previous_window": None,
        "change_count": None,
        "change_percent": None,
        "direction": "not_applicable",
        "confidence": {"level": "High", "reason": reason},
        "stale": False,
        "risk_read": reason,
        "caveat": "No complaint-volume inference is made for a non-applicable company.",
    }


def unavailable_signal(symbol: str, error: str, *, mapping: dict | None = None) -> dict:
    return {
        "found": False,
        "applicable": bool(mapping),
        "symbol": symbol,
        "company": (mapping or {}).get("company"),
        "source": SOURCE_NAME,
        "pulled_at": None,
        "data_through": None,
        "current_window": None,
        "previous_window": None,
        "change_count": None,
        "change_percent": None,
        "direction": "unavailable",
        "confidence": {"level": "Flagged", "reason": "CFPB complaint data is unavailable."},
        "stale": False,
        "error": error,
        "risk_read": "CFPB complaint data is unavailable; no regulatory inference was made.",
        "caveat": "Never infer an absence of complaints from an unavailable source.",
    }


def trend_direction(change_percent: float | None, current: int, previous: int) -> str:
    if previous == 0:
        return "new_activity" if current else "flat"
    if change_percent is None or abs(change_percent) < 10:
        return "stable"
    return "increasing" if change_percent > 0 else "decreasing"


def risk_read(company: str, current: int, previous: int, change_percent: float | None) -> str:
    if change_percent is None:
        trend = "has no prior-window baseline" if current else "shows no complaints in either window"
    elif abs(change_percent) < 10:
        trend = "is broadly stable versus the preceding window"
    elif change_percent > 0:
        trend = f"increased {abs(change_percent):.1f}% versus the preceding window"
    else:
        trend = f"decreased {abs(change_percent):.1f}% versus the preceding window"
    return (
        f"The official CFPB database reports {current:,} complaints for {company} in the current "
        f"window versus {previous:,} previously; volume {trend}. Treat this as an unadjusted "
        "consumer-conduct signal, not a finding of misconduct."
    )


def throttle(interval_seconds: float) -> None:
    global _LAST_REQUEST_AT
    with _LOCK:
        delay = max(0.0, interval_seconds - (time.monotonic() - _LAST_REQUEST_AT))
        if delay:
            time.sleep(delay)
        _LAST_REQUEST_AT = time.monotonic()


def cache_age_seconds(record: dict) -> float:
    value = record.get("last_successful_pull") or record.get("pull_timestamp")
    if not value:
        return float("inf")
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds())
    except ValueError:
        return float("inf")


def normalize_symbol(value: str) -> str:
    return "".join(character for character in str(value).upper() if character.isalnum() or character in ".-")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    temporary.replace(path)
