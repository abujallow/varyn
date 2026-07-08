from __future__ import annotations

import json
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import AGENT_DIR, DATA_DIR
from varyn_settings import load_varyn_settings


_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
}

MAX_AUDIT_LOG_BYTES = 10 * 1024 * 1024
MAX_AUDIT_LOG_LINES = 5000


class AuditLogger:
    def __init__(self, path: Path | None = None) -> None:
        settings = load_varyn_settings().get("audit") or {}
        configured = Path(settings.get("path", "data/audit/varyn-audit.jsonl"))
        self.path = path or (configured if configured.is_absolute() else AGENT_DIR / configured)
        self.summary_path = self.path.parent / "summary.json"
        self.max_recent = max(100, int(settings.get("max_recent_entries", 2000)))
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        event_type: str,
        *,
        session_id: str | None = None,
        reason: str | None = None,
        details: dict | None = None,
    ) -> dict:
        entry = {
            "id": f"audit-{uuid.uuid4().hex[:12]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "session_id": session_id,
            "reason": reason,
            "details": redact(details or {}),
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=True) + "\n")
            self._update_summary(entry)
            self._rotate_if_needed()
        return entry

    def _rotate_if_needed(self) -> None:
        """Cap the append-only log so a long-running instance doesn't grow forever.

        Only triggers past MAX_AUDIT_LOG_BYTES, then trims to the last
        MAX_AUDIT_LOG_LINES entries. Aggregate counters in summary.json are
        unaffected — only the raw recent-entries log is capped.
        """
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size < MAX_AUDIT_LOG_BYTES:
            return
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        trimmed = lines[-MAX_AUDIT_LOG_LINES:]
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            "\n".join(trimmed) + ("\n" if trimmed else ""),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def recent(self, limit: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        with self._lock:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 200)):]
        entries = []
        for line in lines:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    entries.append(value)
            except json.JSONDecodeError:
                continue
        return entries

    def summary(self) -> dict:
        if not self.summary_path.exists():
            return default_summary(self.path)
        try:
            value = json.loads(self.summary_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else default_summary(self.path)
        except (OSError, json.JSONDecodeError):
            return default_summary(self.path)

    def _update_summary(self, entry: dict) -> None:
        summary = self.summary()
        event_type = entry["event_type"]
        counts = Counter(summary.get("event_counts") or {})
        counts[event_type] += 1
        summary["event_counts"] = dict(counts)
        summary["total_events"] = int(summary.get("total_events", 0)) + 1
        summary["last_event_at"] = entry["timestamp"]
        if event_type == "model_request":
            latency = float((entry.get("details") or {}).get("latency_ms") or 0)
            model_requests = int(summary.get("model_requests", 0)) + 1
            latency_total = float(summary.get("model_latency_total_ms", 0)) + latency
            summary["model_requests"] = model_requests
            summary["model_latency_total_ms"] = round(latency_total, 2)
            summary["model_average_latency_ms"] = round(latency_total / model_requests, 2)
        write_json(self.summary_path, summary)


def default_summary(path: Path) -> dict:
    return {
        "version": 1,
        "path": str(path),
        "total_events": 0,
        "event_counts": {},
        "model_requests": 0,
        "model_latency_total_ms": 0,
        "model_average_latency_ms": 0,
        "last_event_at": None,
    }


def redact(value: Any, key: str | None = None) -> Any:
    if key and any(sensitive in key.casefold() for sensitive in _SENSITIVE_KEYS):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {str(item_key): redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return value[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    temporary.replace(path)


_DEFAULT_AUDIT = AuditLogger(DATA_DIR / "audit" / "varyn-audit.jsonl")


def get_audit_logger() -> AuditLogger:
    return _DEFAULT_AUDIT
