from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import AGENT_DIR, DATA_DIR
from market_data_store import (
    atomic_write_json,
    normalize_json,
    read_json,
    record_market_pull,
    update_source_health,
    utc_now,
)


CONFIG_PATH = AGENT_DIR / "varyn.config.json"
SEC_DIR = DATA_DIR / "sec_edgar"
COMPANYFACTS_DIR = SEC_DIR / "companyfacts"
FUNDAMENTALS_DIR = SEC_DIR / "fundamentals"
SUBMISSIONS_DIR = SEC_DIR / "submissions"
SCHEDULE_PATH = SEC_DIR / "schedule.json"

_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0
_STORE_LOCK = threading.RLock()


FIELD_SPECS = {
    "revenue": {
        "units": ("USD",),
        "tags": (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
        ),
    },
    "net_income": {
        "units": ("USD",),
        "tags": ("NetIncomeLoss", "ProfitLoss"),
    },
    "total_assets": {
        "units": ("USD",),
        "tags": ("Assets",),
    },
    "total_liabilities": {
        "units": ("USD",),
        "tags": ("Liabilities",),
    },
    "total_debt": {
        "units": ("USD",),
        "tags": (
            "DebtAndFinanceLeaseObligations",
            "LongTermDebtAndFinanceLeaseObligations",
        ),
    },
    "cash": {
        "units": ("USD",),
        "tags": (
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ),
    },
    "current_assets": {
        "units": ("USD",),
        "tags": ("AssetsCurrent",),
    },
    "current_liabilities": {
        "units": ("USD",),
        "tags": ("LiabilitiesCurrent",),
    },
    "operating_cashflow": {
        "units": ("USD",),
        "tags": ("NetCashProvidedByUsedInOperatingActivities",),
    },
    "shares_outstanding": {
        "units": ("shares",),
        "tags": ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"),
        "taxonomies": ("dei", "us-gaap"),
    },
}

DEBT_COMPONENT_SPECS = {
    "current": (
        "LongTermDebtAndFinanceLeaseObligationsCurrent",
        "LongTermDebtCurrent",
        "ShortTermBorrowings",
    ),
    "noncurrent": (
        "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
        "LongTermDebtNoncurrent",
    ),
}

SEC_CONTEXT_FIELDS = {
    "revenue": "total_revenue",
    "net_income": "net_income",
    "total_assets": "total_assets",
    "total_liabilities": "total_liabilities",
    "total_debt": "total_debt",
    "cash": "total_cash",
    "current_assets": "current_assets",
    "current_liabilities": "current_liabilities",
    "operating_cashflow": "operating_cashflow",
    "shares_outstanding": "shares_outstanding",
}

COMPARABLE_FIELDS = {
    "total_assets",
    "total_liabilities",
    "total_debt",
    "cash",
    "current_assets",
    "current_liabilities",
    "shares_outstanding",
}


def load_sec_config(override: dict | None = None) -> dict:
    defaults = {
        "enabled": True,
        "user_agent": "Varyn Risk Intelligence/0.1 (local educational research; contact: abubakrjallow.vercel.app)",
        "ticker_cik_file": "data/sec_ticker_cik.json",
        "ticker_cik_cache_file": "data/sec_edgar/company_tickers.json",
        "mapping_refresh_seconds": 2_592_000,
        "metadata_refresh_seconds": 86_400,
        "fundamentals_refresh_seconds": 604_800,
        "stale_after_seconds": 34_560_000,
        "request_interval_seconds": 0.5,
        "request_timeout_seconds": 20,
        "fundamental_disagreement_percent": 10.0,
    }
    if override is not None:
        return normalize_sec_config({**defaults, **override})
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        configured = raw.get("sec_edgar") if isinstance(raw, dict) else None
        return normalize_sec_config({**defaults, **configured}) if isinstance(configured, dict) else defaults
    except (OSError, json.JSONDecodeError):
        return defaults


def normalize_sec_config(config: dict) -> dict:
    normalized = dict(config)
    normalized["enabled"] = bool(normalized.get("enabled", True))
    normalized["mapping_refresh_seconds"] = max(86_400, int(normalized["mapping_refresh_seconds"]))
    normalized["metadata_refresh_seconds"] = max(3_600, int(normalized["metadata_refresh_seconds"]))
    normalized["fundamentals_refresh_seconds"] = max(86_400, int(normalized["fundamentals_refresh_seconds"]))
    normalized["stale_after_seconds"] = max(
        normalized["fundamentals_refresh_seconds"], int(normalized["stale_after_seconds"])
    )
    normalized["request_interval_seconds"] = max(0.11, float(normalized["request_interval_seconds"]))
    normalized["request_timeout_seconds"] = max(5, int(normalized["request_timeout_seconds"]))
    normalized["fundamental_disagreement_percent"] = max(
        1.0, float(normalized["fundamental_disagreement_percent"])
    )
    return normalized


def resolve_agent_path(value: str | None, fallback: Path) -> Path:
    if not value:
        return fallback
    path = Path(value)
    return path if path.is_absolute() else AGENT_DIR / path


def load_ticker_cik_mapping(config: dict | None = None) -> dict:
    config = load_sec_config(config)
    static_path = resolve_agent_path(config.get("ticker_cik_file"), DATA_DIR / "sec_ticker_cik.json")
    cache_path = resolve_agent_path(
        config.get("ticker_cik_cache_file"), SEC_DIR / "company_tickers.json"
    )
    entries: dict[str, dict] = {}
    for path in (static_path, cache_path):
        payload = read_json(path, {})
        source_entries = payload.get("entries") or {}
        for symbol, item in source_entries.items():
            if isinstance(item, dict) and item.get("cik"):
                entries[normalize_symbol(symbol)] = item
    return entries


def resolve_cik(symbol: str, config: dict | None = None) -> dict | None:
    item = load_ticker_cik_mapping(config).get(normalize_symbol(symbol))
    if not item:
        return None
    return {
        "symbol": normalize_symbol(symbol),
        "cik": str(item["cik"]).zfill(10),
        "name": item.get("name"),
    }


def refresh_ticker_cik_mapping(
    config: dict | None = None,
    *,
    force: bool = False,
) -> dict:
    config = load_sec_config(config)
    cache_path = resolve_agent_path(
        config.get("ticker_cik_cache_file"), SEC_DIR / "company_tickers.json"
    )
    cached = read_json(cache_path, {})
    if not force and file_is_fresh(cache_path, config["mapping_refresh_seconds"]):
        return {
            "updated": False,
            "entry_count": len(cached.get("entries") or {}),
            "updated_at": cached.get("updated_at"),
        }

    try:
        payload = request_json(
            "https://www.sec.gov/files/company_tickers.json",
            config,
            track_health=True,
        )
        entries = {}
        for item in payload.values():
            if not isinstance(item, dict) or not item.get("ticker") or item.get("cik_str") is None:
                continue
            symbol = normalize_symbol(item["ticker"])
            entries[symbol] = {
                "cik": str(item["cik_str"]).zfill(10),
                "name": item.get("title"),
            }
        result = {
            "version": 1,
            "source": "https://www.sec.gov/files/company_tickers.json",
            "updated_at": utc_now(),
            "entries": entries,
        }
        atomic_write_json(cache_path, result)
        return {"updated": True, "entry_count": len(entries), "updated_at": result["updated_at"]}
    except Exception as exc:
        return {
            "updated": False,
            "entry_count": len(cached.get("entries") or {}),
            "updated_at": cached.get("updated_at"),
            "error": safe_error(exc),
        }


def get_official_fundamentals(
    symbol: str,
    config: dict | None = None,
    *,
    allow_network: bool = True,
    force: bool = False,
) -> dict:
    config = load_sec_config(config)
    symbol = normalize_symbol(symbol)
    cache_path = FUNDAMENTALS_DIR / f"{symbol}.json"
    cached = read_json(cache_path, {})
    if not config["enabled"]:
        return unavailable_result(symbol, "SEC EDGAR integration is disabled in configuration.", cached)

    if cached and not force and file_is_fresh(cache_path, config["fundamentals_refresh_seconds"]):
        return mark_cache_state(cached, config, cache_hit=True)
    if not allow_network:
        return mark_cache_state(cached, config, cache_hit=True) if cached else unavailable_result(
            symbol, "No cached SEC filing data is available.", cached
        )

    identity = resolve_cik(symbol, config)
    if not identity:
        refresh_ticker_cik_mapping(config)
        identity = resolve_cik(symbol, config)
    if not identity:
        return unavailable_result(symbol, "No SEC ticker-to-CIK mapping is available.", cached)

    pulled_at = utc_now()
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{identity['cik']}.json"
    try:
        raw = request_json(url, config, track_health=False)
        atomic_write_json(COMPANYFACTS_DIR / f"CIK{identity['cik']}.json", raw)
        mapped = map_companyfacts(symbol, identity, raw, pulled_at, config)
        atomic_write_json(cache_path, mapped)
        record_sec_pull(symbol, identity, raw, mapped, pulled_at, config)
        return mark_cache_state(mapped, config, cache_hit=False)
    except Exception as exc:
        error = safe_error(exc)
        record_failed_sec_pull(symbol, identity, error, pulled_at, config)
        if cached:
            result = mark_cache_state(cached, config, cache_hit=True)
            result["refresh_error"] = error
            result["stale"] = True
            result["confidence"] = {
                "level": "Low",
                "reason": "Last-known SEC filing data is being used because the refresh failed.",
            }
            return result
        return unavailable_result(symbol, error, cached)


def refresh_watchlist_if_due(symbols: list[str], config: dict | None = None) -> dict:
    config = load_sec_config(config)
    if not config["enabled"]:
        return {"status": "disabled", "checked": 0, "refreshed": 0}

    with _STORE_LOCK:
        schedule = read_json(SCHEDULE_PATH, default_schedule())
        now = datetime.now(timezone.utc)
        next_due = parse_datetime(schedule.get("next_metadata_due"))
        if next_due and now < next_due:
            return {
                "status": "not_due",
                "checked": 0,
                "refreshed": 0,
                "next_metadata_due": schedule.get("next_metadata_due"),
            }

        mapping_result = refresh_ticker_cik_mapping(config)
        checked = 0
        refreshed = 0
        errors = []
        results = {}
        for symbol in dict.fromkeys(normalize_symbol(item) for item in symbols):
            identity = resolve_cik(symbol, config)
            if not identity:
                errors.append(f"{symbol}: no CIK mapping")
                continue
            checked += 1
            metadata = fetch_submissions(identity, config)
            if not metadata.get("success"):
                errors.append(f"{symbol}: {metadata.get('error', 'SEC submissions metadata unavailable')}")
            latest_accession = metadata.get("latest_accession") if metadata.get("success") else None
            cached = read_json(FUNDAMENTALS_DIR / f"{symbol}.json", {})
            cached_accessions = set(cached.get("filing_accessions") or [])
            cache_stale = not cached or not file_is_fresh(
                FUNDAMENTALS_DIR / f"{symbol}.json", config["fundamentals_refresh_seconds"]
            )
            changed = bool(latest_accession and latest_accession not in cached_accessions)
            if cache_stale or changed:
                fundamentals = get_official_fundamentals(symbol, config, force=True)
                if fundamentals.get("found"):
                    refreshed += 1
                elif fundamentals.get("error"):
                    errors.append(f"{symbol}: {fundamentals['error']}")
                results[symbol] = {
                    "refreshed": bool(fundamentals.get("found")),
                    "new_filing": changed,
                    "filing_date": fundamentals.get("latest_filing_date"),
                }
            else:
                results[symbol] = {"refreshed": False, "new_filing": False}

        completed_at = utc_now()
        schedule.update(
            {
                "last_metadata_check": completed_at,
                "next_metadata_due": (
                    now + timedelta(seconds=config["metadata_refresh_seconds"])
                ).isoformat(),
                "last_result": {
                    "checked": checked,
                    "refreshed": refreshed,
                    "errors": errors[:10],
                    "mapping": mapping_result,
                },
            }
        )
        atomic_write_json(SCHEDULE_PATH, schedule)
        return {
            "status": "completed",
            "checked": checked,
            "refreshed": refreshed,
            "errors": errors,
            "results": results,
            "next_metadata_due": schedule["next_metadata_due"],
        }


def fetch_submissions(identity: dict, config: dict) -> dict:
    pulled_at = utc_now()
    try:
        url = f"https://data.sec.gov/submissions/CIK{identity['cik']}.json"
        payload = request_json(url, config, track_health=True)
        atomic_write_json(SUBMISSIONS_DIR / f"CIK{identity['cik']}.json", payload)
        recent = (payload.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        accessions = recent.get("accessionNumber") or []
        filing_dates = recent.get("filingDate") or []
        latest = None
        for index, form in enumerate(forms):
            if str(form).upper() in {"10-K", "10-K/A", "10-Q", "10-Q/A"}:
                latest = {
                    "form": form,
                    "accession": accessions[index] if index < len(accessions) else None,
                    "filing_date": filing_dates[index] if index < len(filing_dates) else None,
                }
                break
        return {
            "success": True,
            "pulled_at": pulled_at,
            "latest_accession": latest.get("accession") if latest else None,
            "latest_filing": latest,
        }
    except Exception as exc:
        return {"success": False, "pulled_at": pulled_at, "error": safe_error(exc)}


def map_companyfacts(
    symbol: str,
    identity: dict,
    raw: dict,
    pulled_at: str,
    config: dict,
) -> dict:
    facts = raw.get("facts") or {}
    fields = {}
    raw_facts = {}
    for field_name, spec in FIELD_SPECS.items():
        selected, candidates = select_latest_fact(facts, spec)
        raw_facts[field_name] = candidates
        fields[field_name] = selected or unavailable_field(field_name)

    if not field_available(fields["total_debt"]):
        current, current_candidates = select_latest_fact(
            facts, {"units": ("USD",), "tags": DEBT_COMPONENT_SPECS["current"]}
        )
        noncurrent, noncurrent_candidates = select_latest_fact(
            facts, {"units": ("USD",), "tags": DEBT_COMPONENT_SPECS["noncurrent"]}
        )
        raw_facts["debt_components"] = {
            "current": current_candidates,
            "noncurrent": noncurrent_candidates,
        }
        if (
            current
            and noncurrent
            and current.get("period_end")
            and current.get("period_end") == noncurrent.get("period_end")
        ):
            fields["total_debt"] = derived_field(
                "total_debt",
                float(current["value"]) + float(noncurrent["value"]),
                "USD",
                [current, noncurrent],
                "Sum of SEC-reported current and noncurrent debt components.",
            )

    current_assets = fields.get("current_assets") or {}
    current_liabilities = fields.get("current_liabilities") or {}
    derived = {}
    if (
        field_available(current_assets)
        and field_available(current_liabilities)
        and current_liabilities.get("value") not in (None, 0)
        and current_assets.get("period_end") == current_liabilities.get("period_end")
    ):
        derived["current_ratio"] = derived_field(
            "current_ratio",
            round(float(current_assets["value"]) / float(current_liabilities["value"]), 4),
            "ratio",
            [current_assets, current_liabilities],
            "Derived from SEC-reported current assets and current liabilities.",
        )

    available = [field for field in fields.values() if field_available(field)]
    filing_dates = sorted({field.get("filing_date") for field in available if field.get("filing_date")})
    filing_accessions = sorted({field.get("accession") for field in available if field.get("accession")})
    latest_filing_date = filing_dates[-1] if filing_dates else None
    stale = filing_is_stale(latest_filing_date, config["stale_after_seconds"])
    confidence = {
        "level": "High" if available and not stale else "Medium" if available else "Flagged",
        "reason": (
            "Official SEC EDGAR companyfacts mapped from filed 10-K/10-Q data."
            if available and not stale
            else "Official SEC filing data is available but may be stale."
            if available
            else "SEC companyfacts returned no confidently mapped required fields."
        ),
        "official_source": True,
    }
    return {
        "version": 1,
        "found": bool(available),
        "symbol": symbol,
        "cik": identity["cik"],
        "entity_name": raw.get("entityName") or identity.get("name"),
        "source": "SEC EDGAR companyfacts",
        "source_url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{identity['cik']}.json",
        "pulled_at": pulled_at,
        "latest_filing_date": latest_filing_date,
        "filing_accessions": filing_accessions,
        "fields": fields,
        "derived": derived,
        "confidence": confidence,
        "stale": stale,
        "disclaimer": "Preliminary financial and risk context, not financial advice or a final credit opinion.",
        "raw_mapped_facts": raw_facts,
    }


def select_latest_fact(facts: dict, spec: dict) -> tuple[dict | None, list[dict]]:
    candidates = []
    taxonomies = spec.get("taxonomies") or ("us-gaap", "dei")
    for taxonomy in taxonomies:
        taxonomy_facts = facts.get(taxonomy) or {}
        for tag_priority, tag in enumerate(spec.get("tags") or ()):
            concept = taxonomy_facts.get(tag) or {}
            units = concept.get("units") or {}
            for unit in spec.get("units") or ():
                for fact in units.get(unit) or []:
                    form = str(fact.get("form") or "").upper()
                    if form not in {"10-K", "10-K/A", "10-Q", "10-Q/A"}:
                        continue
                    value = number_or_none(fact.get("val"))
                    if value is None:
                        continue
                    candidates.append(
                        {
                            "value": value,
                            "unit": unit,
                            "tag": tag,
                            "taxonomy": taxonomy,
                            "form": form,
                            "filing_date": fact.get("filed"),
                            "period_start": fact.get("start"),
                            "period_end": fact.get("end"),
                            "fiscal_year": fact.get("fy"),
                            "fiscal_period": fact.get("fp"),
                            "accession": fact.get("accn"),
                            "frame": fact.get("frame"),
                            "tag_priority": tag_priority,
                        }
                    )
    candidates.sort(
        key=lambda item: (
            item.get("filing_date") or "",
            item.get("period_end") or "",
            1 if item.get("form", "").endswith("/A") else 0,
            -int(item.get("tag_priority", 0)),
        ),
        reverse=True,
    )
    selected = dict(candidates[0]) if candidates else None
    if selected:
        selected.pop("tag_priority", None)
        selected["available"] = True
        selected["source"] = "SEC EDGAR companyfacts"
        selected["confidence"] = "High"
    compact_candidates = []
    seen = set()
    for candidate in candidates:
        key = (candidate.get("tag"), candidate.get("accession"), candidate.get("period_end"))
        if key in seen:
            continue
        seen.add(key)
        compact = dict(candidate)
        compact.pop("tag_priority", None)
        compact_candidates.append(compact)
        if len(compact_candidates) >= 12:
            break
    return selected, compact_candidates


def derived_field(name: str, value: float, unit: str, components: list[dict], method: str) -> dict:
    filing_dates = [item.get("filing_date") for item in components if item.get("filing_date")]
    accessions = [item.get("accession") for item in components if item.get("accession")]
    forms = [item.get("form") for item in components if item.get("form")]
    return {
        "available": True,
        "name": name,
        "value": value,
        "unit": unit,
        "source": "SEC EDGAR companyfacts",
        "confidence": "High",
        "derived": True,
        "method": method,
        "filing_date": max(filing_dates) if filing_dates else None,
        "form": forms[0] if forms and len(set(forms)) == 1 else "+".join(sorted(set(forms))),
        "period_end": components[0].get("period_end") if components else None,
        "accession": accessions[0] if accessions and len(set(accessions)) == 1 else None,
        "component_tags": [item.get("tag") for item in components],
    }


def unavailable_field(name: str) -> dict:
    return {
        "available": False,
        "name": name,
        "value": None,
        "source": "SEC EDGAR companyfacts",
        "confidence": "Unavailable",
        "reason": "No supported SEC XBRL tag was confidently mapped.",
    }


def merge_official_fundamentals(context: dict, fundamentals: dict, config: dict | None = None) -> dict:
    config = load_sec_config(config)
    merged = dict(context)
    merged["official_fundamentals"] = compact_fundamentals(fundamentals)
    merged["fundamentals_found"] = bool(fundamentals.get("found"))
    if not fundamentals.get("found"):
        return merged

    summary_values = {field: merged.get(context_field) for field, context_field in SEC_CONTEXT_FIELDS.items()}
    discrepancies = []
    field_sources = dict(merged.get("field_sources") or {})
    for field_name, context_field in SEC_CONTEXT_FIELDS.items():
        field = (fundamentals.get("fields") or {}).get(field_name) or {}
        if not field_available(field):
            continue
        previous = summary_values.get(field_name)
        if field_name in COMPARABLE_FIELDS and previous not in (None, 0):
            difference = percent_difference(float(previous), float(field["value"]))
            if difference > config["fundamental_disagreement_percent"]:
                discrepancies.append(
                    {
                        "field": field_name,
                        "sec_value": field["value"],
                        "summary_value": previous,
                        "difference_percent": round(difference, 2),
                        "resolution": "SEC EDGAR retained as the official filed value.",
                    }
                )
        merged[context_field] = field["value"]
        field_sources[context_field] = {
            "source": "SEC EDGAR companyfacts",
            "filing_date": field.get("filing_date"),
            "form": field.get("form"),
            "period_end": field.get("period_end"),
            "confidence": field.get("confidence"),
        }

    current_ratio = (fundamentals.get("derived") or {}).get("current_ratio") or {}
    if field_available(current_ratio):
        merged["current_ratio"] = current_ratio["value"]
        field_sources["current_ratio"] = {
            "source": "SEC EDGAR companyfacts",
            "filing_date": current_ratio.get("filing_date"),
            "form": current_ratio.get("form"),
            "period_end": current_ratio.get("period_end"),
            "confidence": current_ratio.get("confidence"),
        }

    confidence = dict(fundamentals.get("confidence") or {})
    if discrepancies:
        confidence = {
            "level": "Flagged",
            "reason": "SEC filed fundamentals conflict materially with one or more summary-source values; SEC values were retained.",
            "official_source": True,
        }
    merged["summary_fundamentals"] = summary_values
    merged["fundamental_discrepancies"] = discrepancies
    merged["fundamentals_confidence"] = confidence
    merged["field_sources"] = field_sources
    return merged


def compact_fundamentals(fundamentals: dict) -> dict:
    """Return the audit-relevant filing data without sending raw XBRL candidates to the model."""
    allowed = {
        "found",
        "symbol",
        "cik",
        "entity_name",
        "source",
        "source_url",
        "pulled_at",
        "latest_filing_date",
        "fields",
        "derived",
        "confidence",
        "stale",
        "cache_hit",
        "refresh_error",
        "disclaimer",
        "error",
    }
    return {key: value for key, value in fundamentals.items() if key in allowed}


def sec_status(config: dict | None = None) -> dict:
    config = load_sec_config(config)
    schedule = read_json(SCHEDULE_PATH, default_schedule())
    mapping = load_ticker_cik_mapping(config)
    cached_symbols = sorted(path.stem for path in FUNDAMENTALS_DIR.glob("*.json")) if FUNDAMENTALS_DIR.exists() else []
    return {
        "ok": True,
        "enabled": config["enabled"],
        "source": "SEC EDGAR",
        "key_required": False,
        "mapping_entries": len(mapping),
        "cached_symbols": cached_symbols,
        "cached_symbol_count": len(cached_symbols),
        "last_metadata_check": schedule.get("last_metadata_check"),
        "next_metadata_due": schedule.get("next_metadata_due"),
        "last_result": schedule.get("last_result"),
        "cadence": {
            "mapping_refresh_seconds": config["mapping_refresh_seconds"],
            "metadata_refresh_seconds": config["metadata_refresh_seconds"],
            "fundamentals_refresh_seconds": config["fundamentals_refresh_seconds"],
        },
        "request_interval_seconds": config["request_interval_seconds"],
    }


def request_json(url: str, config: dict, *, track_health: bool) -> dict:
    global _LAST_REQUEST_AT
    with _REQUEST_LOCK:
        elapsed = time.monotonic() - _LAST_REQUEST_AT
        wait_seconds = config["request_interval_seconds"] - elapsed
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        request = Request(
            url,
            headers={
                "User-Agent": config["user_agent"],
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Host": "data.sec.gov" if "data.sec.gov" in url else "www.sec.gov",
            },
        )
        try:
            with urlopen(request, timeout=config["request_timeout_seconds"]) as response:
                payload = json.loads(response.read().decode("utf-8"))
            _LAST_REQUEST_AT = time.monotonic()
            if track_health:
                update_source_health(
                    "sec_edgar", success=True, error=None, fallback_used=False, timestamp=utc_now()
                )
            return payload
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            _LAST_REQUEST_AT = time.monotonic()
            if track_health:
                update_source_health(
                    "sec_edgar",
                    success=False,
                    error=safe_error(exc),
                    fallback_used=False,
                    timestamp=utc_now(),
                )
            raise


def record_sec_pull(
    symbol: str,
    identity: dict,
    raw: dict,
    mapped: dict,
    pulled_at: str,
    config: dict,
) -> None:
    source_payload = {
        "success": bool(mapped.get("found")),
        "source": "sec_edgar",
        "pulled_at": pulled_at,
        "raw": mapped.get("raw_mapped_facts") or {},
        "cleaned": {
            "entity_name": mapped.get("entity_name"),
            "cik": mapped.get("cik"),
            "latest_filing_date": mapped.get("latest_filing_date"),
            "fields": mapped.get("fields") or {},
            "derived": mapped.get("derived") or {},
        },
        "error": None if mapped.get("found") else "No supported SEC fields were mapped.",
    }
    record_market_pull(
        {
            "ticker": symbol,
            "series_id": f"CIK{identity['cik']}",
            "data_type": "official_fundamentals",
            "refresh_frequency_seconds": config["fundamentals_refresh_seconds"],
            "pull_timestamp": pulled_at,
            "source_name": "sec_edgar" if mapped.get("found") else None,
            "sources": {"sec_edgar": source_payload},
            "raw_source_response": {
                "sec_edgar": {
                    "cik": raw.get("cik"),
                    "entityName": raw.get("entityName"),
                    "mapped_facts": mapped.get("raw_mapped_facts") or {},
                }
            },
            "cleaned_data": source_payload["cleaned"],
            "confidence": mapped.get("confidence"),
            "last_successful_pull": pulled_at if mapped.get("found") else None,
            "error_log": [] if mapped.get("found") else [source_payload["error"]],
            "fallback_source": None,
        }
    )


def record_failed_sec_pull(
    symbol: str,
    identity: dict,
    error: str,
    pulled_at: str,
    config: dict,
) -> None:
    record_market_pull(
        {
            "ticker": symbol,
            "series_id": f"CIK{identity['cik']}",
            "data_type": "official_fundamentals",
            "refresh_frequency_seconds": config["fundamentals_refresh_seconds"],
            "pull_timestamp": pulled_at,
            "source_name": None,
            "sources": {
                "sec_edgar": {
                    "success": False,
                    "source": "sec_edgar",
                    "pulled_at": pulled_at,
                    "raw": {},
                    "cleaned": {},
                    "error": error,
                }
            },
            "raw_source_response": {"sec_edgar": {}},
            "cleaned_data": {},
            "confidence": {"level": "Flagged", "reason": "SEC EDGAR request failed."},
            "last_successful_pull": None,
            "error_log": [error],
            "fallback_source": None,
        }
    )


def mark_cache_state(payload: dict, config: dict, *, cache_hit: bool) -> dict:
    result = normalize_json(payload)
    result["cache_hit"] = cache_hit
    result["stale"] = bool(
        payload.get("stale")
        or filing_is_stale(payload.get("latest_filing_date"), config["stale_after_seconds"])
    )
    return result


def unavailable_result(symbol: str, error: str, cached: dict | None = None) -> dict:
    return {
        "found": False,
        "symbol": normalize_symbol(symbol),
        "source": "SEC EDGAR companyfacts",
        "fields": {},
        "derived": {},
        "confidence": {"level": "Flagged", "reason": error},
        "stale": bool(cached),
        "error": error,
        "disclaimer": "Preliminary financial and risk context, not financial advice or a final credit opinion.",
    }


def default_schedule() -> dict:
    return {
        "version": 1,
        "last_metadata_check": None,
        "next_metadata_due": None,
        "last_result": None,
    }


def file_is_fresh(path: Path, max_age_seconds: int) -> bool:
    if not path.exists():
        return False
    return (time.time() - path.stat().st_mtime) <= max_age_seconds


def filing_is_stale(filing_date: str | None, stale_after_seconds: int) -> bool:
    parsed = parse_datetime(filing_date)
    return True if not parsed else datetime.now(timezone.utc) - parsed > timedelta(seconds=stale_after_seconds)


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def field_available(field: dict | None) -> bool:
    return bool(field and field.get("available") and field.get("value") is not None)


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper().replace(".", "-")


def number_or_none(value: Any) -> float | int | None:
    try:
        number = float(value)
        if number != number:
            return None
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return None


def percent_difference(left: float, right: float) -> float:
    denominator = max(abs(left), abs(right), 0.000001)
    return abs(left - right) / denominator * 100


def safe_error(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        return f"HTTPError {exc.code}: SEC EDGAR request failed."
    if isinstance(exc, URLError):
        return f"URLError: {exc.reason}"
    return f"{type(exc).__name__}: {str(exc)[:220]}"
