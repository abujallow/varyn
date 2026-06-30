from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from market_data_store import latest_source_payload, record_market_pull, utc_now


def fetch_stooq_history(symbol: str) -> dict:
    pulled_at = utc_now()
    try:
        ensure_pandas_datareader_compat()
        from pandas_datareader import data as web

        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=14)
        frame = web.DataReader(stooq_symbol(symbol), "stooq", start=start, end=end)
        if frame.empty:
            raise ValueError("Stooq returned no price history.")
        frame = frame.sort_index()
        raw = []
        for index, row in frame.tail(10).iterrows():
            raw.append(
                {
                    "date": index.date().isoformat(),
                    "open": number_or_none(row.get("Open")),
                    "high": number_or_none(row.get("High")),
                    "low": number_or_none(row.get("Low")),
                    "close": number_or_none(row.get("Close")),
                    "adjusted_close": number_or_none(row.get("Close")),
                    "volume": integer_or_none(row.get("Volume")),
                }
            )
        latest = raw[-1]
        previous = raw[-2] if len(raw) > 1 else latest
        return {
            "success": latest.get("close") is not None,
            "source": "stooq",
            "pulled_at": pulled_at,
            "raw": raw,
            "cleaned": {
                "price": latest.get("close"),
                "previous_close": previous.get("close"),
                "change_percent": percent_change(latest.get("close"), previous.get("close")),
                "volume": latest.get("volume"),
                "price_date": latest.get("date"),
            },
            "error": None,
        }
    except Exception as exc:
        return failed_source("stooq", exc, pulled_at)


def validate_price_sources(
    symbol: str,
    yfinance_payload: dict,
    *,
    refresh_frequency_seconds: int,
    stooq_max_age_seconds: int = 3600,
    agreement_tolerance_percent: float = 0.5,
    minor_difference_percent: float = 2.0,
    stooq_loader: Callable[[str], dict] = fetch_stooq_history,
    force_stooq_refresh: bool = False,
    persist: bool = True,
) -> dict:
    """Choose an honest output and persist the raw + normalized validation record."""
    stooq_payload = None if force_stooq_refresh else cached_stooq_payload(symbol, stooq_max_age_seconds)
    if stooq_payload is None:
        stooq_payload = stooq_loader(symbol)

    yfinance_ok = source_has_price(yfinance_payload)
    stooq_ok = source_has_price(stooq_payload)
    fallback_source = None
    comparison = compare_common_close(yfinance_payload, stooq_payload) if yfinance_ok and stooq_ok else None

    if yfinance_ok and stooq_ok and comparison:
        delta = comparison["difference_percent"]
        if delta <= agreement_tolerance_percent:
            level = "High"
            reason = f"yfinance and Stooq closing prices matched within {agreement_tolerance_percent:.2f}%."
        elif delta <= minor_difference_percent:
            level = "Medium"
            reason = f"yfinance and Stooq differed by {delta:.2f}%, within the review tolerance."
        else:
            level = "Flagged"
            reason = f"yfinance and Stooq differed materially by {delta:.2f}% on {comparison['date']}."
        selected = yfinance_payload
        source_name = "yfinance"
    elif yfinance_ok:
        level = "Medium" if stooq_payload.get("error") is None else "Low"
        reason = "yfinance returned usable data; backup validation was unavailable."
        selected = yfinance_payload
        source_name = "yfinance"
    elif stooq_ok:
        level = "Low"
        reason = "yfinance failed; Stooq supplied the fallback price. Source changed to Stooq."
        selected = stooq_payload
        source_name = "stooq"
        fallback_source = "stooq"
    else:
        level = "Flagged"
        reason = "Neither yfinance nor Stooq returned a usable price."
        selected = {"cleaned": {}}
        source_name = None

    cleaned = dict(selected.get("cleaned") or {})
    confidence = {
        "level": level,
        "reason": reason,
        "difference_percent": comparison.get("difference_percent") if comparison else None,
        "comparison_date": comparison.get("date") if comparison else None,
        "validated_sources": [
            source for source, available in (("yfinance", yfinance_ok), ("stooq", stooq_ok)) if available
        ],
    }
    errors = [
        f"{source}: {payload.get('error')}"
        for source, payload in (("yfinance", yfinance_payload), ("stooq", stooq_payload))
        if payload.get("error")
    ]
    record_payload = {
            "ticker": symbol,
            "data_type": "daily_ohlcv",
            "refresh_frequency_seconds": refresh_frequency_seconds,
            "pull_timestamp": utc_now(),
            "source_name": source_name,
            "sources": {"yfinance": yfinance_payload, "stooq": stooq_payload},
            "raw_source_response": {
                "yfinance": yfinance_payload.get("raw") or [],
                "stooq": stooq_payload.get("raw") or [],
            },
            "cleaned_data": cleaned,
            "confidence": confidence,
            "last_successful_pull": selected.get("pulled_at") if source_name else None,
            "error_log": errors,
            "fallback_source": fallback_source,
        }
    record = record_market_pull(record_payload) if persist else {"record_id": "test-not-persisted"}
    return {
        "found": bool(source_name and cleaned.get("price") is not None),
        "symbol": symbol,
        **cleaned,
        "data_source": source_name,
        "source_changed": bool(fallback_source),
        "fallback_source": fallback_source,
        "sampled_at": selected.get("pulled_at"),
        "confidence": confidence,
        "audit_record_id": record["record_id"],
        "errors": errors,
        "disclaimer": "Preliminary market context, not financial advice.",
    }


def record_unvalidated_yfinance(
    symbol: str,
    payload: dict,
    *,
    refresh_frequency_seconds: int,
) -> dict:
    success = source_has_price(payload)
    confidence = {
        "level": "Medium" if success else "Flagged",
        "reason": "Single yfinance source; not cross-checked in this broad-index batch."
        if success
        else "yfinance did not return a usable price.",
        "difference_percent": None,
        "comparison_date": None,
        "validated_sources": ["yfinance"] if success else [],
    }
    selected = payload.get("cleaned") or {}
    record_market_pull(
        {
            "ticker": symbol,
            "data_type": "daily_ohlcv",
            "refresh_frequency_seconds": refresh_frequency_seconds,
            "pull_timestamp": utc_now(),
            "source_name": "yfinance" if success else None,
            "sources": {"yfinance": payload},
            "raw_source_response": {"yfinance": payload.get("raw") or []},
            "cleaned_data": selected,
            "confidence": confidence,
            "last_successful_pull": payload.get("pulled_at") if success else None,
            "error_log": [payload.get("error")] if payload.get("error") else [],
            "fallback_source": None,
        }
    )
    return confidence


def yfinance_payload_from_bars(symbol: str, bars: list[dict], error: str | None = None) -> dict:
    pulled_at = utc_now()
    usable = [bar for bar in bars if bar.get("close") is not None]
    if error or not usable:
        return {
            "success": False,
            "source": "yfinance",
            "pulled_at": pulled_at,
            "raw": bars,
            "cleaned": {},
            "error": error or f"No usable yfinance price history for {symbol}.",
        }
    latest = usable[-1]
    previous = usable[-2] if len(usable) > 1 else latest
    return {
        "success": True,
        "source": "yfinance",
        "pulled_at": pulled_at,
        "raw": usable,
        "cleaned": {
            "price": latest.get("close"),
            "previous_close": previous.get("close"),
            "change_percent": percent_change(latest.get("close"), previous.get("close")),
            "volume": latest.get("volume"),
            "price_date": latest.get("date"),
        },
        "error": None,
    }


def cached_stooq_payload(symbol: str, max_age_seconds: int) -> dict | None:
    payload = latest_source_payload(symbol, "stooq", successful_only=False)
    if not payload:
        return None
    try:
        pulled = datetime.fromisoformat(payload["pulled_at"])
        if pulled.tzinfo is None:
            pulled = pulled.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - pulled).total_seconds() > max_age_seconds:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return {**payload, "cache_hit": True}


def compare_common_close(primary: dict, backup: dict) -> dict | None:
    primary_by_date = {row.get("date"): row.get("close") for row in primary.get("raw") or []}
    backup_by_date = {row.get("date"): row.get("close") for row in backup.get("raw") or []}
    common_dates = sorted(
        date for date in primary_by_date.keys() & backup_by_date.keys()
        if date and primary_by_date[date] is not None and backup_by_date[date] is not None
    )
    if not common_dates:
        return None
    date = common_dates[-1]
    primary_close = float(primary_by_date[date])
    backup_close = float(backup_by_date[date])
    denominator = max(abs(primary_close), abs(backup_close), 0.000001)
    return {
        "date": date,
        "yfinance_close": round(primary_close, 6),
        "stooq_close": round(backup_close, 6),
        "difference_percent": round(abs(primary_close - backup_close) / denominator * 100, 4),
    }


def source_has_price(payload: dict) -> bool:
    return bool(payload.get("success") and (payload.get("cleaned") or {}).get("price") is not None)


def stooq_symbol(symbol: str) -> str:
    normalized = symbol.upper().replace(".", "-")
    return normalized if normalized.startswith("^") else f"{normalized}.US"


def ensure_pandas_datareader_compat() -> None:
    """Bridge pandas-datareader 0.10's decorator call to pandas 3 without vendoring it."""
    import inspect
    from pandas.util import _decorators

    current = _decorators.deprecate_kwarg
    parameters = list(inspect.signature(current).parameters)
    if not parameters or parameters[0] != "klass" or getattr(current, "_varyn_compat", False):
        return

    def compatible_deprecate_kwarg(*args, **kwargs):
        if args and isinstance(args[0], str):
            return current(FutureWarning, *args, **kwargs)
        return current(*args, **kwargs)

    compatible_deprecate_kwarg._varyn_compat = True
    _decorators.deprecate_kwarg = compatible_deprecate_kwarg


def failed_source(source: str, error: Exception | str, pulled_at: str | None = None) -> dict:
    return {
        "success": False,
        "source": source,
        "pulled_at": pulled_at or utc_now(),
        "raw": [],
        "cleaned": {},
        "error": f"{type(error).__name__}: {error}" if isinstance(error, Exception) else str(error),
    }


def percent_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round(((float(current) - float(previous)) / float(previous)) * 100, 4)


def number_or_none(value) -> float | None:
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError):
        return None


def integer_or_none(value) -> int | None:
    number = number_or_none(value)
    return int(number) if number is not None else None
