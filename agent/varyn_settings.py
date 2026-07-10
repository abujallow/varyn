from __future__ import annotations

import json
import os
from pathlib import Path

from config import AGENT_DIR


CONFIG_PATH = AGENT_DIR / "varyn.config.json"


def load_varyn_settings() -> dict:
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def setting(path: str, default=None):
    value = load_varyn_settings()
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def public_settings() -> dict:
    config = load_varyn_settings()
    heartbeat = config.get("heartbeat") or {}
    provider = config.get("provider") or {}
    runtime = config.get("runtime") or {}
    voice = config.get("voice") or {}
    safety = config.get("safety") or {}
    return {
        "runtime": {
            "backend_host": runtime.get("backend_host", "127.0.0.1"),
            "backend_port": runtime.get("backend_port", 8788),
            "frontend_port": runtime.get("frontend_port", 3200),
            # Render sets this env var automatically on every hosted dyno; it is the
            # same signal security.py's security_required() already trusts to force
            # security on in production. Reused here only to label the HUD's backend
            # status honestly (hosted vs. local) -- no secret or URL is exposed.
            "hosted": bool(os.getenv("RENDER")),
        },
        "provider": {
            "name": "OpenRouter",
            "primary_model": provider.get("primary_model", "openai/gpt-oss-20b:free"),
            "fallback_model": provider.get("fallback_model", "openrouter/free"),
            "model_chain": list(provider.get("model_chain") or []),
        },
        "voice": {
            "input_mode": voice.get("input_mode", "push-to-talk"),
            "push_to_talk_key": voice.get("push_to_talk_key", "Space"),
            "open_mic_enabled": bool(voice.get("open_mic_enabled", True)),
            "choice": voice.get("choice", "browser-default"),
            "preferred_voices": list(voice.get("preferred_voices") or []),
            "rate": float(voice.get("rate", 0.96)),
            "pitch": float(voice.get("pitch", 0.99)),
            "sentence_pause_ms": int(voice.get("sentence_pause_ms", 280)),
            "paragraph_pause_ms": int(voice.get("paragraph_pause_ms", 560)),
            "test_utterance": voice.get("test_utterance", "Varyn voice online."),
            "stt_provider": voice.get("stt_provider", "browser-web-speech"),
            "tts_provider": voice.get("tts_provider", "browser-speech-synthesis"),
            "stop_commands": list(voice.get("stop_commands") or ["stop"]),
        },
        "watchlist": list(heartbeat.get("watchlist") or []),
        "safety": {
            "confirmation_required_actions": list(
                safety.get("confirmation_required_actions") or []
            ),
        },
    }
