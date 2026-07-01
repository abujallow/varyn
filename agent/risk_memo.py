from __future__ import annotations

import base64
import html
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from audit import AuditLogger, get_audit_logger
from config import AGENT_DIR, DATA_DIR
from fred import get_macro_context
from providers import ProviderResult, complete
from sec_edgar import FIELD_SPECS, get_official_fundamentals
from tools.market import extract_symbol, fetch_market_context
from tools.risk import build_risk_analysis
from varyn_settings import setting


_MEMO_DIRECTORY = Path(setting("risk_memo.output_directory", "data/memos"))
MEMO_DIR = _MEMO_DIRECTORY if _MEMO_DIRECTORY.is_absolute() else AGENT_DIR / _MEMO_DIRECTORY
DISCLAIMER = "Preliminary risk assessment - not financial advice or a final credit opinion."
CONFIDENCE_LEVELS = {"High", "Medium", "Low", "Flagged"}
MACRO_SERIES = ("DFF", "DGS10", "DGS2", "T10Y2Y", "CPIAUCSL", "CPILFESL", "UNRATE", "ICSA")
MARKET_FIELDS = (
    ("Price", "price", "currency"),
    ("Daily change", "change_percent", "percent"),
    ("Market capitalization", "market_cap", "currency"),
    ("Beta", "beta", "number"),
    ("Volume", "volume", "integer"),
    ("52-week low", "fifty_two_week_low", "currency"),
    ("52-week high", "fifty_two_week_high", "currency"),
)
FUNDAMENTAL_LABELS = {
    "revenue": "Revenue",
    "net_income": "Net income",
    "total_assets": "Total assets",
    "total_liabilities": "Total liabilities",
    "total_debt": "Total debt",
    "cash": "Cash and equivalents",
    "current_assets": "Current assets",
    "current_liabilities": "Current liabilities",
    "operating_cashflow": "Operating cash flow",
    "shares_outstanding": "Shares outstanding",
}


def prepare_memo_arguments(arguments: dict) -> dict:
    query = str(arguments.get("query") or "").strip()
    symbol = extract_symbol(query)
    if not symbol:
        raise ValueError("A supported company name or ticker is required for a risk memo.")

    generated_at = parse_or_now(arguments.get("generated_at"))
    stamp = generated_at.strftime("%Y%m%d-%H%M%S")
    safe_symbol = re.sub(r"[^A-Z0-9.-]", "", symbol.upper())
    base_name = f"{safe_symbol}-risk-memo-{stamp}"
    return {
        "query": query,
        "symbol": safe_symbol,
        "generated_at": generated_at.isoformat(),
        "markdown_path": str(MEMO_DIR / f"{base_name}.md"),
        "html_path": str(MEMO_DIR / f"{base_name}.html"),
        "pdf_path": str(MEMO_DIR / f"{base_name}.pdf"),
        "base_name": base_name,
    }


def memo_confirmation_description(arguments: dict) -> str:
    prepared = prepare_memo_arguments(arguments)
    return (
        f"Generate an audit-ready risk memo for {prepared['symbol']} in Markdown, HTML, and PDF "
        "and make all three formats available for immediate browser download. Optional local audit "
        "copies may also be written. Nothing will be generated until this exact action is approved."
    )


def export_risk_memo(
    arguments: dict,
    *,
    session_id: str,
    audit: AuditLogger | None = None,
    narrative_provider: Callable[[list[dict]], ProviderResult | str] | None = None,
    force_narrative_unavailable: bool = False,
) -> dict:
    prepared = prepare_memo_arguments(arguments)
    symbol = prepared["symbol"]
    generated_at = prepared["generated_at"]
    markdown_path = trusted_memo_path(prepared["markdown_path"])
    html_path = trusted_memo_path(prepared["html_path"])
    pdf_path = trusted_memo_path(prepared["pdf_path"])

    market = fetch_market_context(symbol, prefer_cache=False)
    fundamentals = (market or {}).get("official_fundamentals") or get_official_fundamentals(symbol)
    macro = get_macro_context("")
    risk = build_risk_analysis(
        f"Assess {symbol} market, credit, liquidity, and operational risk",
        market,
        [market] if market else [],
        macro,
    )
    report = build_report(symbol, generated_at, market or {}, fundamentals, macro, risk)
    narrative, narrative_status, narrative_model = generate_narrative(
        report,
        narrative_provider=narrative_provider,
        force_unavailable=force_narrative_unavailable,
    )
    report["narrative"] = narrative
    report["narrative_status"] = narrative_status
    report["narrative_model"] = narrative_model
    validate_provenance(report)

    markdown_content = render_markdown(report)
    html_content = render_html(report)
    pdf_content = render_pdf(report)

    artifacts, delivery_errors = build_download_artifacts(
        prepared["base_name"],
        markdown_content,
        html_content,
        pdf_content,
    )
    local_copies = persist_optional_audit_copies(
        (
            ("markdown", markdown_path, markdown_content.encode("utf-8")),
            ("html", html_path, html_content.encode("utf-8")),
            ("pdf", pdf_path, pdf_content),
        )
    )

    sources = sorted(
        {
            row["source"]
            for section in ("market_rows", "fundamental_rows", "macro_rows", "risk_rows")
            for row in report[section]
            if row.get("source") and row["source"] != "Not available"
        }
    )
    logger = audit or get_audit_logger()
    logger.log(
        "risk_memo_generated",
        session_id=session_id,
        reason="User approved export of an audit-ready single-company risk memo.",
        details={
            "company": report["company"],
            "symbol": symbol,
            "generated_at": generated_at,
            "sources": sources,
            "local_audit_copies": local_copies,
            "download_formats": [artifact["format"] for artifact in artifacts],
            "delivery_status": delivery_status(artifacts),
            "delivery_errors": delivery_errors,
            "narrative_status": narrative_status,
            "narrative_model": narrative_model,
        },
    )
    return {
        "ok": True,
        "symbol": symbol,
        "company": report["company"],
        "generated_at": generated_at,
        "artifacts": artifacts,
        "delivery_status": delivery_status(artifacts),
        "delivery_errors": delivery_errors,
        "sources": sources,
        "narrative_status": narrative_status,
        "narrative_model": narrative_model,
    }


def build_report(
    symbol: str,
    generated_at: str,
    market: dict,
    fundamentals: dict,
    macro: dict,
    risk: dict,
) -> dict:
    company = market.get("name") or fundamentals.get("entity_name") or symbol
    market_timestamp = market.get("sampled_at") or generated_at
    price_confidence = confidence_level(market.get("confidence"), "Low")
    summary_confidence = "Low" if market.get("stale") else "Medium"
    price_source = market.get("data_source") or "Not available"

    market_rows = []
    for label, key, kind in MARKET_FIELDS:
        is_price_field = key in {"price", "change_percent"}
        value = market.get(key)
        market_rows.append(
            evidence_row(
                label,
                format_value(value, kind),
                price_source if is_price_field else "yfinance summary",
                market_timestamp,
                price_confidence if is_price_field else summary_confidence,
                available=value is not None and bool(market.get("found")),
            )
        )

    fields = fundamentals.get("fields") or {}
    fundamental_rows = []
    for key in FIELD_SPECS:
        field = fields.get(key) or {}
        available = bool(field.get("available"))
        fundamental_rows.append(
            {
                **evidence_row(
                    FUNDAMENTAL_LABELS.get(key, key.replace("_", " ").title()),
                    format_sec_value(field.get("value"), field.get("unit")),
                    field.get("source") or "SEC EDGAR companyfacts",
                    field.get("filing_date") or fundamentals.get("latest_filing_date"),
                    confidence_level(field.get("confidence"), "Flagged"),
                    available=available,
                ),
                "form": field.get("form") or "Not available",
                "period_end": field.get("period_end") or "Not available",
            }
        )
    current_ratio = (fundamentals.get("derived") or {}).get("current_ratio") or {}
    fundamental_rows.append(
        {
            **evidence_row(
                "Current ratio (derived)",
                format_value(current_ratio.get("value"), "number"),
                current_ratio.get("source") or "SEC EDGAR companyfacts",
                current_ratio.get("filing_date") or fundamentals.get("latest_filing_date"),
                confidence_level(current_ratio.get("confidence"), "Flagged"),
                available=bool(current_ratio.get("available")),
            ),
            "form": current_ratio.get("form") or "Not available",
            "period_end": current_ratio.get("period_end") or "Not available",
        }
    )

    macro_lookup = {item.get("id"): item for item in macro.get("series") or []}
    macro_rows = []
    for series_id in MACRO_SERIES:
        item = macro_lookup.get(series_id) or {}
        available = bool(item.get("available"))
        unit = item.get("unit") or ""
        macro_rows.append(
            evidence_row(
                f"{item.get('name') or series_id} ({series_id})",
                format_macro_value(item.get("value"), unit),
                item.get("source") or "Federal Reserve Bank of St. Louis FRED",
                item.get("observation_date"),
                confidence_level(item.get("confidence"), "Flagged"),
                available=available,
            )
        )

    risk_confidence = assess_risk_confidence(market, fundamentals, macro)
    risk_rows = [
        evidence_row(
            "Overall risk score",
            format_value(risk.get("overall_score"), "score"),
            "Local Varyn risk engine",
            generated_at,
            risk_confidence["level"],
            available=risk.get("overall_score") is not None,
        )
    ]
    for module in risk.get("modules") or []:
        risk_rows.append(
            evidence_row(
                module.get("title") or "Risk category",
                format_value(module.get("score"), "score"),
                "Local Varyn risk engine",
                generated_at,
                risk_confidence["level"],
                available=module.get("score") is not None,
            )
        )

    return {
        "company": company,
        "symbol": symbol,
        "generated_at": generated_at,
        "disclaimer": DISCLAIMER,
        "market_rows": market_rows,
        "fundamental_rows": fundamental_rows,
        "macro_rows": macro_rows,
        "risk_rows": risk_rows,
        "risk_confidence": risk_confidence,
        "drivers": list(risk.get("drivers") or []),
        "actions": list(risk.get("actions") or []),
        "macro_risk_read": list(macro.get("risk_read") or []),
        "source_notes": {
            "market": "Market figures are shown only in the stamped market evidence table.",
            "fundamentals": "Official mapped SEC EDGAR companyfacts are shown only in the stamped fundamentals table.",
            "macro": "Official cached FRED observations are shown only in the stamped macro table.",
            "risk confidence": risk_confidence["reason"],
            "narrative independence": "Risk confidence is derived before provider interpretation and does not change when the analyst narrative is unavailable.",
        },
    }


def generate_narrative(
    report: dict,
    *,
    narrative_provider: Callable[[list[dict]], ProviderResult | str] | None = None,
    force_unavailable: bool = False,
) -> tuple[str, str, str | None]:
    unavailable = (
        "Analyst narrative was unavailable at generation time. All deterministic evidence "
        "sections remain complete and should be reviewed directly."
    )
    if force_unavailable:
        return unavailable, "unavailable", None

    packet = qualitative_narrative_packet(report)
    messages = [
        {
            "role": "system",
            "content": (
                "You are Varyn's memo narrative layer. Interpret only the supplied qualitative "
                "evidence. Write two short professional paragraphs. Do not add facts, company "
                "events, figures, dates, prices, percentages, ratios, scores, or any numeric "
                "characters. Do not use markdown headings. State uncertainty plainly."
            ),
        },
        {
            "role": "user",
            "content": "Qualitative evidence packet:\n" + json.dumps(packet, ensure_ascii=True),
        },
    ]
    try:
        raw = narrative_provider(messages) if narrative_provider else complete(messages, tools=None)
        if isinstance(raw, ProviderResult):
            if raw.provider == "local" or not raw.reply.strip():
                return unavailable, "unavailable", raw.model
            narrative = raw.reply.strip()
            model = raw.model
        else:
            narrative = str(raw).strip()
            model = "test-provider"
        if not narrative or re.search(r"\d|[$%]", narrative):
            return (
                "Analyst narrative was withheld because the provider output did not pass "
                "Varyn's numeric-integrity guard. Review the deterministic evidence sections.",
                "withheld_by_integrity_guard",
                model,
            )
        return narrative, "available", model
    except Exception:
        return unavailable, "unavailable", None


def qualitative_narrative_packet(report: dict) -> dict:
    risk_bands = {}
    for row in report["risk_rows"]:
        score = number_or_none(row.get("raw_value"))
        risk_bands[row["metric"]] = risk_band(score)
    market_change = next(
        (number_or_none(row.get("raw_value")) for row in report["market_rows"] if row["metric"] == "Daily change"),
        None,
    )
    available_fundamentals = [
        row["metric"] for row in report["fundamental_rows"] if row["value"] != "Not available"
    ]
    return {
        "company": report["company"],
        "market_direction": "up" if market_change and market_change > 0 else "down" if market_change and market_change < 0 else "flat or unavailable",
        "market_confidence": confidence_summary(report["market_rows"]),
        "official_fundamentals_available": available_fundamentals,
        "fundamentals_confidence": confidence_summary(report["fundamental_rows"]),
        "macro_risk_context": [strip_quantitative_text(item) for item in report["macro_risk_read"]],
        "risk_bands": risk_bands,
        "key_drivers": report["drivers"],
        "recommended_actions": report["actions"],
    }


def render_markdown(report: dict) -> str:
    generated_display = format_display_date(report["generated_at"])
    lines = [
        f"# Varyn Risk Memo: {report['company']} ({report['symbol']})",
        "",
        f"**Generated:** {generated_display}",
        "",
        f"> **{report['disclaimer']}**",
        "",
        "## Deterministic Evidence",
        "",
        "The following sections are populated directly from Varyn's registered data tools. Figures are not written by the reasoning provider.",
        "",
        "### Market Snapshot",
        "",
        markdown_table(report["market_rows"], ("Metric", "Value", "Source", "As of", "Confidence")),
        "",
        "### Official Fundamentals",
        "",
        markdown_table(report["fundamental_rows"], ("Metric", "Value", "Source", "Filing date", "Form", "Confidence")),
        "",
        "### Macro Context",
        "",
        markdown_table(report["macro_rows"], ("Metric", "Value", "Source", "Observation date", "Confidence")),
        "",
        "### Structured Risk Read",
        "",
        markdown_table(report["risk_rows"], ("Metric", "Value", "Source", "As of", "Confidence")),
        "",
        f"**Risk confidence rationale:** {report['risk_confidence']['reason']}",
        "",
        "#### Key Drivers",
        *[f"- {item}" for item in report["drivers"]],
        "",
        "#### Recommended Actions",
        *[f"- {item}" for item in report["actions"]],
        "",
        "## Analyst Narrative - Interpretation",
        "",
        "> This section is provider-composed interpretation over the deterministic evidence above. It is not a source of new figures.",
        "",
        report["narrative"],
        "",
        "## Provenance Notes",
        "",
        *[f"- **{key.title()}:** {value or 'No additional source note was available.'}" for key, value in report["source_notes"].items()],
        "",
        f"**Narrative status:** {report['narrative_status']}",
        "",
    ]
    return "\n".join(lines)


def render_html(report: dict) -> str:
    def table(rows: list[dict], columns: tuple[str, ...]) -> str:
        head = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
        body = "".join(
            "<tr>" + "".join(f"<td>{html.escape(display_cell(row, column))}</td>" for column in columns) + "</tr>"
            for row in rows
        )
        return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"

    drivers = "".join(f"<li>{html.escape(item)}</li>" for item in report["drivers"])
    actions = "".join(f"<li>{html.escape(item)}</li>" for item in report["actions"])
    notes = "".join(
        f"<li><strong>{html.escape(key.title())}:</strong> {html.escape(value or 'No additional source note was available.')}</li>"
        for key, value in report["source_notes"].items()
    )
    narrative = "".join(f"<p>{html.escape(part.strip())}</p>" for part in report["narrative"].split("\n\n") if part.strip())
    generated_display = format_display_date(report["generated_at"])
    risk_confidence_reason = html.escape(report["risk_confidence"]["reason"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Varyn Risk Memo - {html.escape(report['symbol'])}</title>
<style>
:root{{--bg:#020407;--panel:#080d14;--panel-2:#0b121c;--line:#243246;--text:#f4f7fb;--muted:#a8b3c3;--accent:#4ade80;--blue:#7da8d6;}}
*{{box-sizing:border-box}} body{{margin:0;background:radial-gradient(circle at 50% 0,#0d1a2a 0,#020407 42%);color:var(--text);font:15px/1.65 Inter,Segoe UI,Arial,sans-serif}}
main{{width:min(1180px,calc(100% - 32px));margin:32px auto 64px}} header{{border:1px solid var(--line);background:rgba(8,13,20,.94);padding:28px}}
.eyebrow{{color:var(--accent);font-size:12px;font-weight:800;letter-spacing:.14em;text-transform:uppercase}} h1{{margin:.35rem 0 .25rem;font-size:clamp(28px,4vw,44px)}}
.meta,.note{{color:var(--muted)}} .disclaimer{{margin-top:20px;padding:12px 16px;border-left:3px solid var(--accent);background:#07110d;color:#d9fbe5}}
section{{margin-top:20px;border:1px solid var(--line);background:linear-gradient(145deg,rgba(11,18,28,.96),rgba(4,7,11,.96));padding:24px}} h2{{margin:0 0 14px}} h3{{margin:24px 0 10px;color:#dce6f2}}
.table-wrap{{overflow:auto}} table{{width:100%;border-collapse:collapse;min-width:760px}} th,td{{padding:10px 12px;border-bottom:1px solid #1c2837;text-align:left;vertical-align:top}} th{{color:var(--blue);font-size:12px;text-transform:uppercase;letter-spacing:.06em}}
.narrative{{border-color:#37516d;box-shadow:0 0 30px rgba(49,91,132,.12)}} .narrative .label{{color:var(--blue);font-size:12px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}}
ul{{padding-left:20px}} footer{{margin-top:20px;color:var(--muted);font-size:13px}} @media print{{body{{background:#fff;color:#111}} header,section{{background:#fff;border-color:#bbb}} .disclaimer{{color:#111;background:#f3f3f3}}}}
</style></head><body><main>
<header><div class="eyebrow">Varyn Risk Intelligence</div><h1>{html.escape(report['company'])} <span class="meta">({html.escape(report['symbol'])})</span></h1><div class="meta">Generated {html.escape(generated_display)}</div><div class="disclaimer">{html.escape(report['disclaimer'])}</div></header>
<section><h2>Deterministic Evidence</h2><p class="note">Populated directly from registered Varyn tools. Figures are not written by the reasoning provider.</p>
<h3>Market Snapshot</h3>{table(report['market_rows'], ('Metric','Value','Source','As of','Confidence'))}
<h3>Official Fundamentals</h3>{table(report['fundamental_rows'], ('Metric','Value','Source','Filing date','Form','Confidence'))}
<h3>Macro Context</h3>{table(report['macro_rows'], ('Metric','Value','Source','Observation date','Confidence'))}
<h3>Structured Risk Read</h3>{table(report['risk_rows'], ('Metric','Value','Source','As of','Confidence'))}<p class="note"><strong>Risk confidence rationale:</strong> {risk_confidence_reason}</p>
<h3>Key Drivers</h3><ul>{drivers}</ul><h3>Recommended Actions</h3><ul>{actions}</ul></section>
<section class="narrative"><div class="label">Provider Interpretation</div><h2>Analyst Narrative</h2><p class="note">Interpretation over the deterministic evidence above; not a source of new figures.</p>{narrative}</section>
<section><h2>Provenance Notes</h2><ul>{notes}</ul><p class="note">Narrative status: {html.escape(report['narrative_status'])}</p></section>
<footer>Generated {html.escape(generated_display)} · {html.escape(DISCLAIMER)}</footer></main></body></html>"""


def render_pdf(report: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=13 * mm,
        bottomMargin=13 * mm,
        title=f"Varyn Risk Memo - {report['symbol']}",
        author="Varyn Risk Intelligence",
    )
    palette = {
        "ink": colors.HexColor("#111827"),
        "muted": colors.HexColor("#526071"),
        "line": colors.HexColor("#CBD5E1"),
        "panel": colors.HexColor("#F5F7FA"),
        "accent": colors.HexColor("#166534"),
        "accent_soft": colors.HexColor("#DCFCE7"),
        "navy": colors.HexColor("#0F2742"),
        "white": colors.white,
    }
    base = getSampleStyleSheet()
    styles = {
        "eyebrow": ParagraphStyle(
            "MemoEyebrow",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=9,
            textColor=palette["accent"],
            spaceAfter=3,
            uppercase=True,
        ),
        "title": ParagraphStyle(
            "MemoTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=21,
            leading=24,
            alignment=TA_LEFT,
            textColor=palette["ink"],
            spaceAfter=4,
        ),
        "meta": ParagraphStyle(
            "MemoMeta",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=palette["muted"],
        ),
        "section": ParagraphStyle(
            "MemoSection",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=15,
            textColor=palette["navy"],
            spaceBefore=10,
            spaceAfter=5,
        ),
        "subsection": ParagraphStyle(
            "MemoSubsection",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=9.5,
            leading=12,
            textColor=palette["ink"],
            spaceBefore=7,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "MemoBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.3,
            leading=11.5,
            textColor=palette["ink"],
            spaceAfter=4,
        ),
        "note": ParagraphStyle(
            "MemoNote",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=10,
            textColor=palette["muted"],
            spaceAfter=4,
        ),
        "cell": ParagraphStyle(
            "MemoCell",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=6.9,
            leading=8.4,
            textColor=palette["ink"],
        ),
        "cell_header": ParagraphStyle(
            "MemoCellHeader",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=6.7,
            leading=8,
            textColor=palette["white"],
            alignment=TA_CENTER,
        ),
    }

    def paragraph(value, style="body"):
        return Paragraph(html.escape(str(value or "Not available")), styles[style])

    def evidence_table(rows: list[dict], columns: tuple[str, ...], weights: tuple[float, ...]):
        header = [paragraph(column, "cell_header") for column in columns]
        body = [
            [paragraph(display_cell(row, column), "cell") for column in columns]
            for row in rows
        ]
        total = sum(weights)
        widths = [document.width * weight / total for weight in weights]
        table = Table([header, *body], colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), palette["navy"]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.35, palette["line"]),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [palette["white"], palette["panel"]]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        return table

    generated_display = format_display_date(report["generated_at"])
    story = [
        paragraph("VARYN RISK INTELLIGENCE", "eyebrow"),
        Paragraph(
            f"{html.escape(report['company'])} <font color='#526071'>({html.escape(report['symbol'])})</font>",
            styles["title"],
        ),
        paragraph(f"Generated {generated_display}", "meta"),
        Spacer(1, 4 * mm),
        Table(
            [[paragraph(report["disclaimer"], "body")]],
            colWidths=[document.width],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), palette["accent_soft"]),
                    ("BOX", (0, 0), (-1, -1), 0.8, palette["accent"]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 9),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            ),
        ),
        paragraph("Deterministic Evidence", "section"),
        paragraph(
            "Populated directly from registered Varyn tools. Figures are not written by the reasoning provider.",
            "note",
        ),
        paragraph("Market Snapshot", "subsection"),
        evidence_table(report["market_rows"], ("Metric", "Value", "Source", "As of", "Confidence"), (1.3, 1, 1.7, 1.35, 0.8)),
        paragraph("Official Fundamentals", "subsection"),
        evidence_table(report["fundamental_rows"], ("Metric", "Value", "Source", "Filing date", "Form", "Confidence"), (1.25, 1, 1.65, 1.2, 0.55, 0.75)),
        paragraph("Macro Context", "subsection"),
        evidence_table(report["macro_rows"], ("Metric", "Value", "Source", "Observation date", "Confidence"), (1.55, 0.9, 1.8, 1.25, 0.75)),
        paragraph("Structured Risk Read", "subsection"),
        evidence_table(report["risk_rows"], ("Metric", "Value", "Source", "As of", "Confidence"), (1.4, 0.9, 1.7, 1.3, 0.75)),
        paragraph(f"Risk confidence rationale: {report['risk_confidence']['reason']}", "note"),
        paragraph("Key Drivers", "subsection"),
        *[paragraph(f"- {item}") for item in report["drivers"]],
        paragraph("Recommended Actions", "subsection"),
        *[paragraph(f"- {item}") for item in report["actions"]],
        paragraph("Analyst Narrative", "section"),
        Table(
            [[[
                paragraph("PROVIDER INTERPRETATION", "eyebrow"),
                paragraph(
                    "Interpretation over the deterministic evidence above; not a source of new figures.",
                    "note",
                ),
                *[paragraph(part) for part in report["narrative"].split("\n\n") if part.strip()],
            ]]],
            colWidths=[document.width],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), palette["panel"]),
                    ("BOX", (0, 0), (-1, -1), 0.8, palette["line"]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 9),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            ),
        ),
        paragraph("Provenance Notes", "section"),
        *[
            paragraph(f"{key.title()}: {value or 'No additional source note was available.'}")
            for key, value in report["source_notes"].items()
        ],
        paragraph(f"Narrative status: {report['narrative_status']}", "note"),
    ]

    def page_footer(canvas, _document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(palette["muted"])
        canvas.drawString(12 * mm, 7 * mm, "Varyn Risk Intelligence - preliminary, not financial advice")
        canvas.drawRightString(landscape(A4)[0] - 12 * mm, 7 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    document.build(story, onFirstPage=page_footer, onLaterPages=page_footer)
    return buffer.getvalue()


def build_download_artifacts(
    base_name: str,
    markdown_content: str,
    html_content: str,
    pdf_content: bytes,
) -> tuple[list[dict], list[str]]:
    payloads = (
        ("markdown", f"{base_name}.md", "text/markdown;charset=utf-8", markdown_content.encode("utf-8")),
        ("html", f"{base_name}.html", "text/html;charset=utf-8", html_content.encode("utf-8")),
        ("pdf", f"{base_name}.pdf", "application/pdf", pdf_content),
    )
    artifacts = []
    errors = []
    for format_name, filename, mime_type, content in payloads:
        try:
            artifacts.append(
                {
                    "format": format_name,
                    "filename": filename,
                    "mime_type": mime_type,
                    "encoding": "base64",
                    "content": base64.b64encode(content).decode("ascii"),
                    "size_bytes": len(content),
                }
            )
        except Exception:
            errors.append(f"{format_name.upper()} content could not be prepared for browser download.")
    return artifacts, errors


def delivery_status(artifacts: list[dict]) -> str:
    if len(artifacts) == 3:
        return "ready"
    return "partial" if artifacts else "unavailable"


def persist_optional_audit_copies(entries) -> dict:
    results = {}
    for format_name, path, content in entries:
        try:
            write_bytes_atomic(path, content)
            results[format_name] = {"written": True, "path": str(path)}
        except OSError:
            results[format_name] = {"written": False, "path": str(path)}
    return results


def evidence_row(metric: str, value: str, source: str | None, date: str | None, confidence: str, *, available: bool) -> dict:
    return {
        "metric": metric,
        "value": value if available else "Not available",
        "raw_value": value if available else None,
        "source": source or "Not available",
        "date": date or "Not available",
        "confidence": confidence_level(confidence, "Flagged") if available else "Flagged",
    }


def markdown_table(rows: list[dict], columns: tuple[str, ...]) -> str:
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(escape_markdown(display_cell(row, column)) for column in columns) + " |" for row in rows]
    return "\n".join([header, separator, *body])


def display_cell(row: dict, column: str) -> str:
    mapping = {
        "Metric": "metric",
        "Value": "value",
        "Source": "source",
        "As of": "date",
        "Filing date": "date",
        "Observation date": "date",
        "Form": "form",
        "Confidence": "confidence",
    }
    value = str(row.get(mapping[column]) or "Not available")
    return format_display_date(value) if column in {"As of", "Filing date", "Observation date"} else value


def assess_risk_confidence(market: dict, fundamentals: dict, macro: dict) -> dict:
    market_level = confidence_level(market.get("confidence"), "Flagged")
    fundamentals_level = confidence_level(fundamentals.get("confidence"), "Flagged")
    macro_level = confidence_level(macro.get("confidence"), "Flagged")
    reasons = []

    if market_level == "Flagged":
        reasons.append("Market pricing was flagged because usable or agreeing price evidence was unavailable.")
    elif market_level == "Low":
        reasons.append("Market-price confidence is Low because independent backup validation was unavailable or the retained quote was degraded.")
    elif market_level == "Medium":
        reasons.append("Market pricing relies on a single usable source or a minor cross-source difference.")

    if market.get("stale"):
        reasons.append("The market observation is stale.")

    required_fields = ("total_debt", "cash", "current_assets", "current_liabilities")
    mapped_fields = fundamentals.get("fields") or {}
    missing_material_fundamentals = any(
        not (mapped_fields.get(field_name) or {}).get("available")
        for field_name in required_fields
    )
    if fundamentals_level == "Flagged":
        reasons.append("Official fundamentals were flagged or could not be mapped confidently.")
    elif fundamentals_level == "Low":
        reasons.append("Official fundamentals are being retained with Low confidence after a failed refresh.")
    if missing_material_fundamentals:
        reasons.append("Some material credit and liquidity fields are unavailable from the current generic SEC mapping.")

    if macro_level == "Flagged":
        reasons.append("Official macro context was flagged or unavailable.")
    elif macro_level == "Low":
        reasons.append("Macro context includes stale or retained observations.")

    if "Flagged" in {market_level, fundamentals_level, macro_level}:
        level = "Flagged"
    elif "Low" in {market_level, fundamentals_level, macro_level} or missing_material_fundamentals:
        level = "Low"
    else:
        level = "Medium"
        reasons.append("The deterministic risk score remains a preliminary framework even when underlying evidence is current and official.")

    return {
        "level": level,
        "reason": " ".join(dict.fromkeys(reasons)),
        "inputs": {
            "market": market_level,
            "fundamentals": fundamentals_level,
            "macro": macro_level,
        },
        "narrative_independent": True,
    }


def confidence_summary(rows: list[dict]) -> str:
    levels = {row.get("confidence") for row in rows if row.get("value") != "Not available"}
    for level in ("Flagged", "Low", "Medium", "High"):
        if level in levels:
            return level
    return "Flagged"


def confidence_level(value, fallback: str) -> str:
    level = value.get("level") if isinstance(value, dict) else value
    clean = str(level or fallback).title()
    return clean if clean in CONFIDENCE_LEVELS else fallback


def validate_provenance(report: dict) -> None:
    errors = []
    for section in ("market_rows", "fundamental_rows", "macro_rows", "risk_rows"):
        for row in report.get(section) or []:
            metric = row.get("metric") or "Unnamed metric"
            if row.get("value") == "Not available":
                if row.get("confidence") != "Flagged":
                    errors.append(f"{section}/{metric}: unavailable value must be Flagged")
                continue
            if not row.get("source") or row.get("source") == "Not available":
                errors.append(f"{section}/{metric}: source missing")
            if not row.get("date") or row.get("date") == "Not available":
                errors.append(f"{section}/{metric}: relevant date missing")
            if row.get("confidence") not in CONFIDENCE_LEVELS:
                errors.append(f"{section}/{metric}: confidence missing or invalid")
    if errors:
        raise ValueError("Memo provenance validation failed: " + "; ".join(errors))


def format_display_date(value: str | None) -> str:
    if not value or value == "Not available":
        return "Not available"
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text

    has_time = "T" in text or bool(re.search(r"\d{1,2}:\d{2}", text))
    if not has_time:
        return strip_leading_day_zero(parsed.strftime("%B %d, %Y"))

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    rendered = parsed.strftime("%B %d, %Y, %I:%M %p UTC")
    return strip_leading_day_zero(rendered).replace(", 0", ", ")


def strip_leading_day_zero(value: str) -> str:
    return re.sub(r"\b0(?=\d\b)", "", value)


def format_sec_value(value, unit: str | None) -> str:
    if value is None:
        return "Not available"
    if unit == "USD":
        return format_value(value, "currency")
    if unit == "shares":
        return f"{float(value):,.0f} shares"
    return format_value(value, "number")


def format_macro_value(value, unit: str) -> str:
    if value is None:
        return "Not available"
    suffix = "%" if unit == "percent" else f" {unit}" if unit else ""
    return f"{float(value):,.3f}".rstrip("0").rstrip(".") + suffix


def format_value(value, kind: str) -> str:
    number = number_or_none(value)
    if number is None:
        return "Not available"
    if kind == "currency":
        return f"${number:,.2f}" if abs(number) < 1_000_000 else f"${number:,.0f}"
    if kind == "percent":
        return f"{number:+.2f}%"
    if kind == "integer":
        return f"{number:,.0f}"
    if kind == "score":
        return f"{number:.0f} / 100"
    return f"{number:,.4f}".rstrip("0").rstrip(".")


def number_or_none(value) -> float | None:
    try:
        if isinstance(value, str):
            match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", value)
            if not match:
                return None
            value = match.group(0).replace(",", "")
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None


def risk_band(score: float | None) -> str:
    if score is None:
        return "unavailable"
    if score >= 70:
        return "elevated"
    if score >= 50:
        return "moderate"
    return "lower"


def strip_quantitative_text(value: str) -> str:
    text = re.sub(r"\b\d+(?:\.\d+)?%?\b", "the latest observed level", str(value))
    return re.sub(r"\s+", " ", text).strip()


def parse_or_now(value) -> datetime:
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).replace(microsecond=0)
        except ValueError:
            pass
    return datetime.now(timezone.utc).replace(microsecond=0)


def trusted_memo_path(value: str) -> Path:
    root = MEMO_DIR.resolve()
    path = Path(value).resolve()
    if path.parent != root or path.suffix.lower() not in {".md", ".html", ".pdf"}:
        raise ValueError("Memo export path is outside the approved local memo folder.")
    return path


def write_text_atomic(path: Path, value: str) -> None:
    write_bytes_atomic(path, value.encode("utf-8"))


def write_bytes_atomic(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


def escape_markdown(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
