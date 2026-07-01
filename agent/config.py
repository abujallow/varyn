from __future__ import annotations

import os
import json
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
AGENT_DIR = Path(__file__).resolve().parent
DATA_DIR = AGENT_DIR / "data"


def load_base_config() -> dict:
    path = AGENT_DIR / "varyn.config.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


BASE_CONFIG = load_base_config()
PROVIDER_CONFIG = BASE_CONFIG.get("provider") or {}
DEFAULT_OPENROUTER_MODEL = PROVIDER_CONFIG.get("primary_model", "openai/gpt-oss-20b:free")
DEFAULT_OPENROUTER_FALLBACK_MODEL = PROVIDER_CONFIG.get("fallback_model", "openrouter/free")
DEFAULT_OPENROUTER_MODEL_CHAIN = PROVIDER_CONFIG.get("model_chain") or [
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_OPENROUTER_FALLBACK_MODEL,
]


load_dotenv(ROOT_DIR / ".env.local", override=False)
load_dotenv(ROOT_DIR / "agent.env", override=False)
load_dotenv(AGENT_DIR / ".env", override=False)


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "") or DEFAULT_OPENROUTER_MODEL
OPENROUTER_FALLBACK_MODEL = (
    os.getenv("OPENROUTER_FALLBACK_MODEL", "") or DEFAULT_OPENROUTER_FALLBACK_MODEL
)
_CHAIN_OVERRIDE = os.getenv("OPENROUTER_MODEL_CHAIN", "")
OPENROUTER_MODEL_CHAIN = tuple(
    dict.fromkeys(
        model.strip()
        for model in (
            _CHAIN_OVERRIDE.split(",")
            if _CHAIN_OVERRIDE
            else [OPENROUTER_MODEL, *DEFAULT_OPENROUTER_MODEL_CHAIN[1:]]
        )
        if model.strip()
    )
)
PROVIDER_TIMEOUT_SECONDS = int(
    os.getenv("VARYN_PROVIDER_TIMEOUT_SECONDS", str(PROVIDER_CONFIG.get("timeout_seconds", 35)))
)
PROVIDER_RETRIES = int(
    os.getenv(
        "VARYN_PROVIDER_RETRIES",
        str(PROVIDER_CONFIG.get("retries_per_model", PROVIDER_CONFIG.get("retries", 1))),
    )
)
PROVIDER_BACKOFF_SECONDS = float(
    os.getenv("VARYN_PROVIDER_BACKOFF_SECONDS", str(PROVIDER_CONFIG.get("backoff_seconds", 0.35)))
)
PROVIDER_MAX_ATTEMPTS = int(
    os.getenv("VARYN_PROVIDER_MAX_ATTEMPTS", str(PROVIDER_CONFIG.get("max_attempts_total", 6)))
)
PROVIDER_MAX_TOTAL_SECONDS = float(
    os.getenv("VARYN_PROVIDER_MAX_TOTAL_SECONDS", str(PROVIDER_CONFIG.get("max_total_seconds", 42)))
)
PROVIDER_CATALOG_TIMEOUT_SECONDS = float(
    os.getenv("VARYN_PROVIDER_CATALOG_TIMEOUT_SECONDS", str(PROVIDER_CONFIG.get("catalog_timeout_seconds", 4)))
)
PROVIDER_CATALOG_CACHE_SECONDS = int(
    os.getenv("VARYN_PROVIDER_CATALOG_CACHE_SECONDS", str(PROVIDER_CONFIG.get("catalog_cache_seconds", 86400)))
)
MAX_AGENT_STEPS = int(
    os.getenv("VARYN_MAX_AGENT_STEPS", str(PROVIDER_CONFIG.get("max_agent_steps", 5)))
)


def safe_config_snapshot() -> dict:
    return {
        "gemini_key_exists": bool(GEMINI_API_KEY),
        "gemini_model": GEMINI_MODEL,
        "openrouter_key_exists": bool(OPENROUTER_API_KEY),
        "openrouter_model": OPENROUTER_MODEL,
        "openrouter_fallback_model": OPENROUTER_FALLBACK_MODEL,
        "openrouter_model_chain": list(OPENROUTER_MODEL_CHAIN),
        "provider_timeout_seconds": PROVIDER_TIMEOUT_SECONDS,
        "provider_retries_per_model": PROVIDER_RETRIES,
        "provider_backoff_seconds": PROVIDER_BACKOFF_SECONDS,
        "provider_max_attempts": PROVIDER_MAX_ATTEMPTS,
        "provider_max_total_seconds": PROVIDER_MAX_TOTAL_SECONDS,
    }
