from __future__ import annotations

import json
import re

from config import AGENT_DIR
from market_data_store import latest_source_payload
from market_validation import validate_price_sources, yfinance_payload_from_bars
from sec_edgar import get_official_fundamentals, merge_official_fundamentals
from sp500_cache import cached_market_context, resolve_constituent_name


COMPANY_TO_TICKER = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "tesla": "TSLA",
    "nvidia": "NVDA",
    "amazon": "AMZN",
    "ford": "F",
    "general motors": "GM",
    "gm": "GM",
    "toyota": "TM",
    "jpmorgan chase": "JPM",
    "bank of america": "BAC",
    "m&t bank": "MTB",
    "m and t bank": "MTB",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "meta": "META",
    "jpmorgan": "JPM",
    "goldman": "GS",
    "dow jones": "^DJI",
    "nasdaq": "^IXIC",
    "s&p": "^GSPC",
    "sp500": "^GSPC",
}

CONNECTOR_WORDS = {"and", "or", "vs", "versus", "with"}
QUERY_STOPWORDS = {
    "analyze",
    "analysis",
    "assess",
    "compare",
    "risk",
    "market",
    "credit",
    "liquidity",
    "operational",
    "ratio",
    "ratios",
    "for",
    "of",
    "the",
    "a",
    "an",
    "give",
    "me",
    "show",
    "review",
}


def extract_symbols(message: str) -> list[str]:
    lowered = message.lower()
    symbols: list[str] = []
    for name, ticker in COMPANY_TO_TICKER.items():
        if name in lowered:
            symbols.append(ticker)

    explicit = re.findall(r"\b[A-Z]{1,5}\b", message)
    ignored = {"I", "AI", "CEO", "CFO", "USA", "USD", "API", "VAR", "PDF", "CSV", "JSON", "HTML", "CSS"}
    for token in explicit:
        if token not in ignored:
            symbols.append(token)

    if not symbols:
        for query in extract_company_queries(message):
            symbol = resolve_company_query(query)
            if symbol:
                symbols.append(symbol)

    deduped = []
    for symbol in symbols:
        if symbol not in deduped:
            deduped.append(symbol)
    return deduped[:5]


def extract_company_queries(message: str) -> list[str]:
    cleaned = re.sub(r"[?.!]", " ", message)
    cleaned = re.sub(r"\b(compare|analyze|assess|evaluate|review|risk|analysis|credit ratio analysis|market risk|liquidity risk)\b", " ", cleaned, flags=re.I)
    parts = re.split(r",|/|\b(?:and|vs|versus)\b", cleaned, flags=re.I)
    queries: list[str] = []
    for part in parts:
        words = [word for word in re.findall(r"[A-Za-z][A-Za-z&.'-]*", part) if word.lower() not in QUERY_STOPWORDS and word.lower() not in CONNECTOR_WORDS]
        query = " ".join(words).strip()
        if len(query) >= 3 and query.lower() not in COMPANY_TO_TICKER:
            queries.append(query)
    return queries[:6]


def resolve_company_query(query: str) -> str | None:
    lowered = query.lower()
    if lowered in COMPANY_TO_TICKER:
        return COMPANY_TO_TICKER[lowered]

    cached_symbol = resolve_constituent_name(query, load_sp500_config())
    if cached_symbol:
        return cached_symbol

    try:
        import yfinance as yf

        if hasattr(yf, "Search"):
            search = yf.Search(query, max_results=5)
            quotes = getattr(search, "quotes", None) or []
        else:
            quotes = []

        for quote in quotes:
            symbol = quote.get("symbol")
            quote_type = (quote.get("quoteType") or quote.get("typeDisp") or "").upper()
            exchange = (quote.get("exchange") or "").upper()
            if symbol and quote_type in {"EQUITY", "ETF"} and exchange not in {"CCY", "CCC"}:
                return symbol
    except Exception:
        return None

    return None


def extract_symbol(message: str) -> str | None:
    symbols = extract_symbols(message)
    return symbols[0] if symbols else None


def get_market_context(message: str) -> dict | None:
    symbol = extract_symbol(message)
    if not symbol:
        return None
    return fetch_market_context(symbol, prefer_cache=not needs_detailed_market_data(message))


def get_market_contexts(message: str) -> list[dict]:
    prefer_cache = not needs_detailed_market_data(message)
    return [
        fetch_market_context(symbol, prefer_cache=prefer_cache)
        for symbol in extract_symbols(message)
    ]


def fetch_market_context(symbol: str, prefer_cache: bool = True) -> dict:
    data_layer = load_data_layer_config()
    if prefer_cache:
        cached = cached_market_context(symbol, load_sp500_config())
        if cached is not None:
            yfinance_payload = latest_source_payload(symbol, "yfinance")
            if yfinance_payload is not None:
                yfinance_payload = {**yfinance_payload, "cache_hit": True}
            if yfinance_payload is None and cached.get("found"):
                yfinance_payload = yfinance_payload_from_bars(
                    symbol,
                    [
                        {
                            "date": date_from_timestamp(cached.get("sampled_at")),
                            "close": cached.get("price"),
                            "adjusted_close": cached.get("price"),
                            "volume": None,
                        }
                    ],
                )
            if yfinance_payload is not None:
                validated = validate_price_sources(
                    symbol,
                    yfinance_payload,
                    refresh_frequency_seconds=data_layer["watchlist_refresh_frequency_seconds"],
                    stooq_max_age_seconds=data_layer["stooq_validation_interval_seconds"],
                    agreement_tolerance_percent=data_layer["agreement_tolerance_percent"],
                    minor_difference_percent=data_layer["minor_difference_percent"],
                )
                return {**cached, **validated, "cached": True}
            return cached

    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        history = ticker.history(period="5d")
        info = ticker.info or {}
        if history.empty:
            raise ValueError("No price history returned.")

        last_close = float(history["Close"].iloc[-1])
        previous_close = float(history["Close"].iloc[-2]) if len(history) > 1 else last_close
        change_percent = ((last_close - previous_close) / previous_close) * 100 if previous_close else 0
        volume = int(history["Volume"].iloc[-1]) if "Volume" in history and not history["Volume"].empty else None

        bars = history_bars(history)
        validated = validate_price_sources(
            symbol,
            yfinance_payload_from_bars(symbol, bars),
            refresh_frequency_seconds=data_layer["watchlist_refresh_frequency_seconds"],
            stooq_max_age_seconds=data_layer["stooq_validation_interval_seconds"],
            agreement_tolerance_percent=data_layer["agreement_tolerance_percent"],
            minor_difference_percent=data_layer["minor_difference_percent"],
        )
        context = {
            **validated,
            "name": info.get("shortName") or info.get("longName"),
            "price": round(float(validated.get("price") or last_close), 2),
            "previous_close": round(float(validated.get("previous_close") or previous_close), 2),
            "change_percent": round(float(validated.get("change_percent") if validated.get("change_percent") is not None else change_percent), 2),
            "market_cap": info.get("marketCap"),
            "beta": info.get("beta"),
            "volume": volume,
            "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
            "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
            "total_debt": info.get("totalDebt"),
            "total_cash": info.get("totalCash"),
            "current_ratio": info.get("currentRatio"),
            "quick_ratio": info.get("quickRatio"),
            "debt_to_equity": info.get("debtToEquity"),
            "operating_cashflow": info.get("operatingCashflow"),
            "total_revenue": info.get("totalRevenue"),
            "net_income": info.get("netIncomeToCommon"),
        }
        fundamentals = get_official_fundamentals(symbol)
        return merge_official_fundamentals(context, fundamentals)
    except Exception as exc:
        fallback = validate_price_sources(
            symbol,
            yfinance_payload_from_bars(symbol, [], f"{type(exc).__name__}: {exc}"),
            refresh_frequency_seconds=data_layer["watchlist_refresh_frequency_seconds"],
            stooq_max_age_seconds=data_layer["stooq_validation_interval_seconds"],
            agreement_tolerance_percent=data_layer["agreement_tolerance_percent"],
            minor_difference_percent=data_layer["minor_difference_percent"],
        )
        if fallback.get("found"):
            return fallback
        return {**fallback, "error": str(exc)}


def needs_detailed_market_data(message: str) -> bool:
    lowered = message.lower()
    return any(
        phrase in lowered
        for phrase in (
            "risk analysis",
            "analyze",
            "assess",
            "credit",
            "liquidity",
            "ratio",
            "debt",
            "cash flow",
            "market cap",
            "beta",
            "52-week",
            "52 week",
            "revenue",
            "net income",
            "assets",
            "liabilities",
            "fundamental",
            "financial statement",
            "sec",
            "filing",
        )
    )


def load_sp500_config() -> dict:
    config_path = AGENT_DIR / "varyn.config.json"
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        config = raw.get("sp500")
        return config if isinstance(config, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_data_layer_config() -> dict:
    defaults = {
        "stooq_validation_interval_seconds": 3600,
        "agreement_tolerance_percent": 0.5,
        "minor_difference_percent": 2.0,
        "watchlist_refresh_frequency_seconds": 300,
    }
    config_path = AGENT_DIR / "varyn.config.json"
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        config = raw.get("data_layer")
        return {**defaults, **config} if isinstance(config, dict) else defaults
    except (OSError, json.JSONDecodeError):
        return defaults


def history_bars(history) -> list[dict]:
    bars = []
    for index, row in history.iterrows():
        close = number_or_none(row.get("Close"))
        if close is None:
            continue
        bars.append(
            {
                "date": index.date().isoformat(),
                "close": close,
                "adjusted_close": number_or_none(row.get("Adj Close")) or close,
                "volume": integer_or_none(row.get("Volume")),
            }
        )
    return bars


def date_from_timestamp(value: str | None) -> str:
    if not value:
        return "unknown"
    return value[:10]


def number_or_none(value):
    try:
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None


def integer_or_none(value):
    number = number_or_none(value)
    return int(number) if number is not None else None
