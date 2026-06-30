from __future__ import annotations

import re

from fred import get_macro_context


RISK_TERMS = [
    "risk",
    "market",
    "credit",
    "liquidity",
    "operational",
    "scenario",
    "merger",
    "acquisition",
    "portfolio",
    "var",
    "volatility",
    "analyze",
    "assess",
]

STRUCTURED_ANALYSIS_TERMS = [
    "analyze",
    "assess",
    "assessment",
    "risk assessment",
    "scenario analysis",
    "market scan",
    "credit memo",
    "due diligence",
    "stress test",
    "run analysis",
    "generate analysis",
    "build a memo",
    "prepare a memo",
    "evaluate",
]

CONCEPTUAL_PREFIXES = [
    "what is",
    "what are",
    "explain",
    "define",
    "tell me about",
    "how does",
    "why does",
]

ANALYSIS_HINTS = [
    "analyze",
    "analysis",
    "assess",
    "assessment",
    "compare",
    "credit ratio",
    "ratio analysis",
    "risk profile",
    "risk analysis",
    "market risk",
    "liquidity risk",
    "credit risk of",
    "evaluate",
]


def is_risk_request(message: str) -> bool:
    lowered = message.lower()
    is_conceptual = any(lowered.strip().startswith(prefix) for prefix in CONCEPTUAL_PREFIXES)
    if is_conceptual and not any(term in lowered for term in STRUCTURED_ANALYSIS_TERMS):
        return False

    has_analysis_intent = any(term in lowered for term in STRUCTURED_ANALYSIS_TERMS + ANALYSIS_HINTS)
    has_transaction_intent = any(term in lowered for term in ["merger", "acquisition", "portfolio", "ticker", "company", "stock"])
    has_var_intent = bool(re.search(r"\bvar\b", lowered)) and has_analysis_intent

    return has_analysis_intent or has_transaction_intent or has_var_intent


def score_from_context(message: str, market_context: dict | None) -> dict:
    lowered = message.lower()
    market = 50
    credit = 45
    liquidity = 45
    operational = 45

    if market_context and market_context.get("found"):
        change = abs(float(market_context.get("change_percent") or 0))
        market += min(25, int(change * 4))

    if any(term in lowered for term in ["merger", "acquisition", "debt", "credit", "funding"]):
        credit += 22
        liquidity += 12

    if any(term in lowered for term in ["cash", "liquidity", "runway", "current ratio", "quick ratio"]):
        liquidity += 24

    if any(term in lowered for term in ["operations", "operational", "supply chain", "vendor", "cyber"]):
        operational += 24

    if any(term in lowered for term in ["volatile", "volatility", "market", "dow", "nasdaq", "s&p"]):
        market += 18

    return {
        "Market Risk": min(market, 95),
        "Credit Risk": min(credit, 95),
        "Liquidity Risk": min(liquidity, 95),
        "Operational Risk": min(operational, 95),
    }


def format_metric(value) -> str:
    if value is None:
        return "Unavailable from free source"
    if isinstance(value, (int, float)) and abs(value) > 1_000_000:
        return f"{value:,.0f}"
    return str(value)


def build_risk_analysis(
    message: str,
    market_context: dict | None,
    market_contexts: list[dict] | None = None,
    macro_context: dict | None = None,
) -> dict:
    scores = score_from_context(message, market_context)
    overall = round(sum(scores.values()) / len(scores))
    symbol = market_context.get("symbol") if market_context else None
    context_count = len([context for context in (market_contexts or []) if context])
    comparison_mode = context_count > 1 or "compare" in message.lower()
    subject = "Multi-Company Risk Comparison" if comparison_mode else symbol or "requested scenario"

    market_detail = "Market data is not connected for this request."
    if market_context:
        if market_context.get("found"):
            market_detail = (
                f"{subject} last traded near {market_context.get('price')} with a "
                f"{market_context.get('change_percent')}% recent move."
            )
        else:
            market_detail = f"Market lookup attempted for {subject}, but no usable price data was returned."

    official = (market_context or {}).get("official_fundamentals") or {}
    official_fields = official.get("fields") or {}
    official_ratio = (official.get("derived") or {}).get("current_ratio") or {}
    official_label = official_filing_label(official)
    credit_detail = "Review leverage, refinancing exposure, counterparty quality, and downside debt-service capacity."
    liquidity_detail = "Evaluate cash runway, working-capital needs, funding access, and asset convertibility under stress."
    if official.get("found"):
        debt = format_metric((official_fields.get("total_debt") or {}).get("value"))
        cash = format_metric((official_fields.get("cash") or {}).get("value"))
        ratio = format_metric(official_ratio.get("value"))
        credit_detail = f"Official filed debt: {debt}; cash: {cash}. {official_label}"
        liquidity_detail = f"SEC-derived current ratio: {ratio}; filed cash: {cash}. {official_label}"

    macro_context = macro_context or get_macro_context(message)
    macro_reads = macro_context.get("risk_read") or []
    macro_label = macro_context_label(macro_context)
    if macro_reads:
        credit_detail = f"{credit_detail} Macro context: {macro_reads[0]} {macro_label}"
        liquidity_detail = f"{liquidity_detail} Macro context: {' '.join(macro_reads[:2])} {macro_label}"

    modules = [
        {
            "title": "Market Risk",
            "score": str(scores["Market Risk"]),
            "detail": market_detail,
        },
        {
            "title": "Credit Risk",
            "score": str(scores["Credit Risk"]),
            "detail": credit_detail,
        },
        {
            "title": "Liquidity Risk",
            "score": str(scores["Liquidity Risk"]),
            "detail": liquidity_detail,
        },
        {
            "title": "Operational Risk",
            "score": str(scores["Operational Risk"]),
            "detail": "Assess process resilience, vendor concentration, cyber exposure, continuity planning, and key-person dependency.",
        },
    ]

    data_points = []
    for context in market_contexts or ([market_context] if market_context else []):
        if not context:
            continue
        data_points.append(
            {
                "symbol": context.get("symbol"),
                "name": context.get("name"),
                "source": (
                    f"{context.get('data_source') or 'free market source'} | Confidence: "
                    f"{(context.get('confidence') or {}).get('level', 'unrated')}"
                ) if context.get("found") else "Unavailable from free source",
                "price": format_metric(context.get("price")),
                "change_percent": format_metric(context.get("change_percent")),
                "market_cap": format_metric(context.get("market_cap")),
                "beta": format_metric(context.get("beta")),
                "volume": format_metric(context.get("volume")),
                "fifty_two_week_range": (
                    f"{format_metric(context.get('fifty_two_week_low'))} - {format_metric(context.get('fifty_two_week_high'))}"
                ),
                "debt_to_equity": format_metric(context.get("debt_to_equity")),
                "current_ratio": format_metric(context.get("current_ratio")),
                "quick_ratio": format_metric(context.get("quick_ratio")),
                "total_debt": format_metric(context.get("total_debt")),
                "total_cash": format_metric(context.get("total_cash")),
                "operating_cashflow": format_metric(context.get("operating_cashflow")),
                "revenue": format_metric(context.get("total_revenue")),
                "net_income": format_metric(context.get("net_income")),
                "fundamentals_source": official_filing_label(context.get("official_fundamentals") or {}),
                "fundamentals_confidence": (context.get("fundamentals_confidence") or {}).get("level", "Unavailable"),
                "fundamental_discrepancies": context.get("fundamental_discrepancies") or [],
            }
        )

    return {
        "title": subject if comparison_mode else f"{subject} Risk Assessment",
        "overall_score": overall,
        "summary": (
            f"Preliminary overall risk score: {overall}. Live/free data is used where available; "
            f"unavailable fields are labelled. {macro_reads[0] if macro_reads else 'FRED macro context is unavailable.'}"
        ),
        "location": "Local Varyn risk engine",
        "source": "local risk engine",
        "macro_context": macro_context,
        "data_points": data_points,
        "drivers": [
            "Market movement and volatility context",
            "Balance-sheet and funding sensitivity",
            "Liquidity runway and short-term flexibility",
            "Operational resilience and dependency exposure",
            "Official FRED rates, yield-curve, inflation, and labor context",
        ],
        "modules": modules,
        "actions": [
            "Validate live data sources",
            "Separate facts from assumptions",
            "Run downside scenario analysis",
            "Prepare an executive risk brief",
        ],
    }


def macro_context_label(macro_context: dict) -> str:
    if not macro_context.get("found"):
        return "FRED macro context unavailable."
    confidence = (macro_context.get("confidence") or {}).get("level", "Unrated")
    pulled_at = macro_context.get("pulled_at") or "timestamp unavailable"
    return f"Source: FRED; confidence: {confidence}; cache update: {pulled_at}."


def official_filing_label(fundamentals: dict) -> str:
    if not fundamentals.get("found"):
        return "Official SEC fundamentals unavailable."
    fields = fundamentals.get("fields") or {}
    available = [field for field in fields.values() if field.get("available")]
    forms = sorted({field.get("form") for field in available if field.get("form")})
    filing_dates = sorted(
        {field.get("filing_date") for field in available if field.get("filing_date")}
    )
    form = "/".join(forms) if forms else "filing"
    filing_date = filing_dates[-1] if filing_dates else fundamentals.get("latest_filing_date") or "date unavailable"
    confidence = (fundamentals.get("confidence") or {}).get("level", "Unrated")
    return f"SEC EDGAR {form}, filed {filing_date}; confidence: {confidence}."
