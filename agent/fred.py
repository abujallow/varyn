from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config import AGENT_DIR, DATA_DIR, FRED_API_KEY
from market_data_store import (
    atomic_write_json,
    read_json,
    record_market_pull,
    update_source_health,
    utc_now,
)


CONFIG_PATH = AGENT_DIR / "varyn.config.json"
FRED_DIR = DATA_DIR / "fred"
SNAPSHOT_PATH = FRED_DIR / "macro_snapshot.json"
SCHEDULE_PATH = FRED_DIR / "schedule.json"
BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

_REQUEST_LOCK = threading.Lock()
_REFRESH_LOCK = threading.RLock()
_LAST_REQUEST_AT = 0.0


DEFAULT_SERIES = [
    {"id": "DFF", "name": "Federal Funds Effective Rate", "frequency": "daily", "unit": "percent"},
    {"id": "DGS10", "name": "10-Year Treasury Yield", "frequency": "daily", "unit": "percent"},
    {"id": "DGS2", "name": "2-Year Treasury Yield", "frequency": "daily", "unit": "percent"},
    {"id": "T10Y2Y", "name": "10Y-2Y Treasury Spread", "frequency": "daily", "unit": "percentage points"},
    {"id": "CPIAUCSL", "name": "Consumer Price Index", "frequency": "monthly", "unit": "index"},
    {"id": "CPILFESL", "name": "Core Consumer Price Index", "frequency": "monthly", "unit": "index"},
    {"id": "UNRATE", "name": "Unemployment Rate", "frequency": "monthly", "unit": "percent"},
    {"id": "ICSA", "name": "Initial Jobless Claims", "frequency": "weekly", "unit": "claims"},
    {"id": "GDP", "name": "Gross Domestic Product", "frequency": "quarterly", "unit": "billions USD"},
    {"id": "INDPRO", "name": "Industrial Production Index", "frequency": "monthly", "unit": "index"},
    {"id": "UMCSENT", "name": "Consumer Sentiment", "frequency": "monthly", "unit": "index"},
]


def load_fred_config(override: dict | None = None) -> dict:
    defaults = {
        "enabled": True,
        "snapshot_file": "data/fred/macro_snapshot.json",
        "schedule_file": "data/fred/schedule.json",
        "refresh_interval_seconds": 21_600,
        "request_interval_seconds": 0.25,
        "request_timeout_seconds": 20,
        "observation_limit": 12,
        "series": DEFAULT_SERIES,
    }
    if override is None:
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            override = raw.get("fred") if isinstance(raw, dict) else None
        except (OSError, json.JSONDecodeError):
            override = None
    return normalize_fred_config({**defaults, **(override or {})})


def normalize_fred_config(config: dict) -> dict:
    normalized = dict(config)
    normalized["enabled"] = bool(normalized.get("enabled", True))
    normalized["refresh_interval_seconds"] = max(3_600, int(normalized["refresh_interval_seconds"]))
    normalized["request_interval_seconds"] = max(0.1, float(normalized["request_interval_seconds"]))
    normalized["request_timeout_seconds"] = max(5, int(normalized["request_timeout_seconds"]))
    normalized["observation_limit"] = max(2, min(36, int(normalized["observation_limit"])))
    series = []
    for item in normalized.get("series") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        series.append(
            {
                "id": str(item["id"]).strip().upper(),
                "name": str(item.get("name") or item["id"]).strip(),
                "frequency": str(item.get("frequency") or "unknown").strip().lower(),
                "unit": str(item.get("unit") or "value").strip(),
            }
        )
    normalized["series"] = series
    return normalized


def refresh_if_due(config: dict | None = None, *, force: bool = False) -> dict:
    config = load_fred_config(config)
    snapshot_path = resolve_agent_path(config.get("snapshot_file"), SNAPSHOT_PATH)
    schedule_path = resolve_agent_path(config.get("schedule_file"), SCHEDULE_PATH)
    if not config["enabled"]:
        return {"status": "disabled", "checked": 0, "updated": 0}
    if not FRED_API_KEY:
        update_source_health(
            "fred",
            success=False,
            error="FRED_API_KEY is not configured.",
            fallback_used=False,
            timestamp=utc_now(),
        )
        return {"status": "unconfigured", "checked": 0, "updated": 0, "error": "FRED API key is not configured."}

    with _REFRESH_LOCK:
        schedule = read_json(schedule_path, default_schedule())
        now = datetime.now(timezone.utc)
        next_due = parse_datetime(schedule.get("next_refresh_due"))
        if not force and next_due and now < next_due:
            return {
                "status": "not_due",
                "checked": 0,
                "updated": 0,
                "next_refresh_due": schedule.get("next_refresh_due"),
            }

        previous = read_json(snapshot_path, default_snapshot())
        previous_series = previous.get("series") or {}
        results: dict[str, dict] = {}
        errors = []
        updated = 0
        for definition in config["series"]:
            series_id = definition["id"]
            try:
                raw = fetch_observations(series_id, config)
                normalized = normalize_observations(definition, raw, config)
                results[series_id] = normalized
                record_fred_pull(definition, raw, normalized, config)
                updated += 1
            except Exception as exc:
                error = safe_error(exc)
                errors.append(f"{series_id}: {error}")
                retained = dict(previous_series.get(series_id) or unavailable_series(definition))
                retained["stale"] = True
                retained["refresh_error"] = error
                retained["confidence"] = {
                    "level": "Low" if retained.get("available") else "Flagged",
                    "reason": "Last-known FRED value retained because the refresh failed."
                    if retained.get("available")
                    else "FRED returned no usable observation.",
                    "official_source": True,
                }
                results[series_id] = retained
                record_failed_fred_pull(definition, error, config)

        completed_at = utc_now()
        snapshot = {
            "version": 1,
            "source": "Federal Reserve Bank of St. Louis FRED",
            "source_url": "https://fred.stlouisfed.org/",
            "pulled_at": completed_at,
            "series": results,
            "confidence": aggregate_confidence(results),
            "errors": errors,
            "disclaimer": "Preliminary macroeconomic risk context, not financial advice.",
        }
        atomic_write_json(snapshot_path, snapshot)
        schedule = {
            "version": 1,
            "last_refresh": completed_at,
            "next_refresh_due": (
                now + timedelta(seconds=config["refresh_interval_seconds"])
            ).isoformat(),
            "last_result": {
                "checked": len(config["series"]),
                "updated": updated,
                "errors": errors,
            },
        }
        atomic_write_json(schedule_path, schedule)
        return {
            "status": "completed",
            "checked": len(config["series"]),
            "updated": updated,
            "errors": errors,
            "next_refresh_due": schedule["next_refresh_due"],
        }


def get_macro_snapshot(config: dict | None = None) -> dict:
    config = load_fred_config(config)
    snapshot_path = resolve_agent_path(config.get("snapshot_file"), SNAPSHOT_PATH)
    snapshot = read_json(snapshot_path, default_snapshot())
    snapshot["configured"] = bool(FRED_API_KEY)
    snapshot["enabled"] = config["enabled"]
    snapshot["series_count"] = len(snapshot.get("series") or {})
    return snapshot


def get_macro_context(query: str = "", config: dict | None = None) -> dict:
    snapshot = get_macro_snapshot(config)
    series = snapshot.get("series") or {}
    selected_ids = select_series_ids(query, list(series))
    selected = [compact_series(series[series_id]) for series_id in selected_ids if series_id in series]
    return {
        "found": bool(selected),
        "query": query,
        "source": snapshot.get("source") or "Federal Reserve Bank of St. Louis FRED",
        "pulled_at": snapshot.get("pulled_at"),
        "series": selected,
        "confidence": aggregate_confidence({item["id"]: item for item in selected}),
        "risk_read": build_macro_risk_read(series),
        "errors": snapshot.get("errors") or [],
        "disclaimer": "Preliminary macroeconomic risk context, not financial advice.",
    }


def fred_status(config: dict | None = None) -> dict:
    config = load_fred_config(config)
    schedule_path = resolve_agent_path(config.get("schedule_file"), SCHEDULE_PATH)
    schedule = read_json(schedule_path, default_schedule())
    snapshot = get_macro_snapshot(config)
    return {
        "ok": True,
        "enabled": config["enabled"],
        "configured": bool(FRED_API_KEY),
        "source": "FRED",
        "series_configured": len(config["series"]),
        "series_cached": len(snapshot.get("series") or {}),
        "last_refresh": schedule.get("last_refresh"),
        "next_refresh_due": schedule.get("next_refresh_due"),
        "last_result": schedule.get("last_result"),
        "refresh_interval_seconds": config["refresh_interval_seconds"],
        "request_interval_seconds": config["request_interval_seconds"],
    }


def fetch_observations(series_id: str, config: dict) -> dict:
    params = urlencode(
        {
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": config["observation_limit"],
        }
    )
    return request_json(f"{BASE_URL}?{params}", config)


def normalize_observations(definition: dict, raw: dict, config: dict) -> dict:
    observations = []
    for item in raw.get("observations") or []:
        value = number_or_none(item.get("value"))
        if value is None:
            continue
        observations.append({"date": item.get("date"), "value": value})
    if not observations:
        raise ValueError("No usable FRED observations returned.")
    latest = observations[0]
    previous = observations[1] if len(observations) > 1 else None
    change = round(latest["value"] - previous["value"], 6) if previous else None
    stale_after_seconds = stale_seconds_for_frequency(definition["frequency"])
    stale = observation_is_stale(latest.get("date"), stale_after_seconds)
    return {
        "available": True,
        "id": definition["id"],
        "name": definition["name"],
        "frequency": definition["frequency"],
        "unit": definition["unit"],
        "value": latest["value"],
        "observation_date": latest.get("date"),
        "previous_value": previous.get("value") if previous else None,
        "previous_observation_date": previous.get("date") if previous else None,
        "change": change,
        "direction": direction_label(change),
        "source": "Federal Reserve Bank of St. Louis FRED",
        "pulled_at": utc_now(),
        "stale": stale,
        "stale_after_seconds": stale_after_seconds,
        "confidence": {
            "level": "Medium" if stale else "High",
            "reason": "Official FRED observation is stale for its configured release frequency."
            if stale
            else "Official FRED observation is current for its configured release frequency.",
            "official_source": True,
        },
        "recent_observations": observations,
        "refresh_frequency_seconds": config["refresh_interval_seconds"],
    }


def record_fred_pull(definition: dict, raw: dict, normalized: dict, config: dict) -> None:
    pulled_at = normalized["pulled_at"]
    record_market_pull(
        {
            "ticker": definition["id"],
            "series_id": definition["id"],
            "data_type": "official_macro_series",
            "refresh_frequency_seconds": config["refresh_interval_seconds"],
            "pull_timestamp": pulled_at,
            "source_name": "fred",
            "sources": {
                "fred": {
                    "success": True,
                    "source": "fred",
                    "pulled_at": pulled_at,
                    "raw": raw,
                    "cleaned": normalized,
                    "error": None,
                }
            },
            "raw_source_response": {"fred": raw},
            "cleaned_data": normalized,
            "confidence": normalized["confidence"],
            "last_successful_pull": pulled_at,
            "error_log": [],
            "fallback_source": None,
        }
    )


def record_failed_fred_pull(definition: dict, error: str, config: dict) -> None:
    pulled_at = utc_now()
    record_market_pull(
        {
            "ticker": definition["id"],
            "series_id": definition["id"],
            "data_type": "official_macro_series",
            "refresh_frequency_seconds": config["refresh_interval_seconds"],
            "pull_timestamp": pulled_at,
            "source_name": None,
            "sources": {
                "fred": {
                    "success": False,
                    "source": "fred",
                    "pulled_at": pulled_at,
                    "raw": {},
                    "cleaned": {},
                    "error": error,
                }
            },
            "raw_source_response": {"fred": {}},
            "cleaned_data": {},
            "confidence": {"level": "Flagged", "reason": "FRED request failed."},
            "last_successful_pull": None,
            "error_log": [error],
            "fallback_source": None,
        }
    )


def build_macro_risk_read(series: dict[str, dict]) -> list[str]:
    reads = []
    fed_funds = series.get("DFF") or {}
    spread = series.get("T10Y2Y") or {}
    unemployment = series.get("UNRATE") or {}
    inflation = series.get("CPIAUCSL") or {}
    claims = series.get("ICSA") or {}
    if fed_funds.get("available"):
        direction = fed_funds.get("direction")
        reads.append(
            f"Policy rate {direction or 'stable'} at {fed_funds.get('value')}%; elevated or rising rates can tighten funding and valuation conditions."
        )
    if spread.get("available"):
        value = float(spread.get("value") or 0)
        if value < 0:
            reads.append(f"The 10Y-2Y spread is inverted at {value} percentage points, a cautionary growth and bank-margin signal.")
        else:
            reads.append(f"The 10Y-2Y spread is {value} percentage points, indicating a positively sloped curve at the latest observation.")
    if unemployment.get("available"):
        reads.append(
            f"Unemployment is {unemployment.get('value')}% and {unemployment.get('direction')}; labor conditions inform household and credit risk."
        )
    if inflation.get("available"):
        reads.append(
            f"CPI is {inflation.get('direction')}; this index-level direction is context, not an annual inflation-rate calculation."
        )
    if claims.get("available") and claims.get("direction") == "rising":
        reads.append("Initial claims are rising versus the prior observation, which can indicate softening labor conditions.")
    return reads


def select_series_ids(query: str, available_ids: list[str]) -> list[str]:
    text = query.lower().replace("–", "-").replace("—", "-")
    aliases = {
        "DFF": ("fed funds", "federal funds", "policy rate"),
        "DGS10": ("10-year", "10 year", "10y", "treasury yield"),
        "DGS2": ("2-year", "2 year", "2y"),
        "T10Y2Y": ("10y-2y", "10y 2y", "yield curve", "treasury spread", "curve"),
        "CPIAUCSL": ("cpi", "inflation"),
        "CPILFESL": ("core cpi", "core inflation"),
        "UNRATE": ("unemployment", "labor market", "jobs"),
        "ICSA": ("jobless claims", "initial claims", "claims"),
        "GDP": ("gdp", "economic growth"),
        "INDPRO": ("industrial production", "production"),
        "UMCSENT": ("consumer sentiment", "sentiment"),
    }
    selected = [series_id for series_id, terms in aliases.items() if any(term in text for term in terms)]
    return selected or list(available_ids)


def compact_series(item: dict) -> dict:
    return {
        key: value
        for key, value in item.items()
        if key not in {"recent_observations", "refresh_frequency_seconds", "stale_after_seconds"}
    }


def aggregate_confidence(series: dict[str, dict]) -> dict:
    available = [item for item in series.values() if item.get("available")]
    if not available:
        return {"level": "Flagged", "reason": "No cached FRED observations are available.", "official_source": True}
    levels = {str((item.get("confidence") or {}).get("level")) for item in available}
    if "Flagged" in levels:
        level = "Flagged"
    elif "Low" in levels:
        level = "Low"
    elif "Medium" in levels:
        level = "Medium"
    else:
        level = "High"
    return {
        "level": level,
        "reason": f"{len(available)} official FRED series available; freshness is evaluated by release frequency.",
        "official_source": True,
    }


def request_json(url: str, config: dict) -> dict:
    global _LAST_REQUEST_AT
    with _REQUEST_LOCK:
        elapsed = time.monotonic() - _LAST_REQUEST_AT
        wait_seconds = config["request_interval_seconds"] - elapsed
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        request = Request(url, headers={"User-Agent": "Varyn Risk Intelligence/0.1", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=config["request_timeout_seconds"]) as response:
                payload = json.loads(response.read().decode("utf-8"))
            _LAST_REQUEST_AT = time.monotonic()
            return payload
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            _LAST_REQUEST_AT = time.monotonic()
            raise


def resolve_agent_path(value: str | None, fallback: Path) -> Path:
    if not value:
        return fallback
    path = Path(value)
    return path if path.is_absolute() else AGENT_DIR / path


def observation_is_stale(value: str | None, max_age_seconds: int) -> bool:
    parsed = parse_datetime(value)
    return True if not parsed else datetime.now(timezone.utc) - parsed > timedelta(seconds=max_age_seconds)


def stale_seconds_for_frequency(frequency: str) -> int:
    return {
        "daily": 7 * 86_400,
        "weekly": 21 * 86_400,
        "monthly": 70 * 86_400,
        "quarterly": 220 * 86_400,
    }.get(frequency, 70 * 86_400)


def direction_label(change: float | None) -> str | None:
    if change is None:
        return None
    if abs(change) < 1e-9:
        return "unchanged"
    return "rising" if change > 0 else "falling"


def unavailable_series(definition: dict) -> dict:
    return {
        "available": False,
        "id": definition["id"],
        "name": definition["name"],
        "frequency": definition["frequency"],
        "unit": definition["unit"],
        "value": None,
        "source": "Federal Reserve Bank of St. Louis FRED",
        "confidence": {"level": "Flagged", "reason": "No FRED observation is available.", "official_source": True},
        "stale": True,
    }


def default_snapshot() -> dict:
    return {
        "version": 1,
        "source": "Federal Reserve Bank of St. Louis FRED",
        "pulled_at": None,
        "series": {},
        "confidence": {"level": "Flagged", "reason": "Awaiting the first FRED refresh."},
        "errors": [],
    }


def default_schedule() -> dict:
    return {"version": 1, "last_refresh": None, "next_refresh_due": None, "last_result": None}


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def number_or_none(value: Any) -> float | int | None:
    try:
        number = float(value)
        if number != number:
            return None
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return None


def safe_error(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        return f"HTTPError {exc.code}: FRED request failed."
    if isinstance(exc, URLError):
        return f"URLError: {str(exc.reason)[:160]}"
    return f"{type(exc).__name__}: {str(exc)[:200]}"
