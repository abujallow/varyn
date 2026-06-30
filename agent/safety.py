from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from audit import AuditLogger, get_audit_logger
from config import DATA_DIR
from varyn_settings import load_varyn_settings


STATE_PATH = DATA_DIR / "safety_state.json"
CONFIRMATIONS_PATH = DATA_DIR / "confirmations.json"


class SafetyRails:
    def __init__(
        self,
        state_path: Path = STATE_PATH,
        confirmations_path: Path = CONFIRMATIONS_PATH,
        audit: AuditLogger | None = None,
    ) -> None:
        self.state_path = state_path
        self.confirmations_path = confirmations_path
        self.audit = audit or get_audit_logger()
        self._lock = threading.RLock()
        if not self.state_path.exists():
            write_json(self.state_path, default_state())
        if not self.confirmations_path.exists():
            write_json(self.confirmations_path, {"version": 1, "confirmations": []})

    def requires_confirmation(self, action: str) -> bool:
        required = self.settings().get("confirmation_required_actions") or []
        return action in set(required)

    def request_confirmation(
        self,
        *,
        session_id: str,
        action: str,
        arguments: dict,
        description: str | None = None,
        action_kind: str = "tool",
    ) -> dict:
        now = datetime.now(timezone.utc)
        expiry = max(60, int(self.settings().get("confirmation_expiry_seconds", 600)))
        confirmation = {
            "id": f"confirm-{uuid.uuid4().hex[:12]}",
            "session_id": session_id,
            "action": action,
            "action_kind": action_kind,
            "arguments": arguments,
            "what_it_will_do": description or self.action_description(action, arguments),
            "status": "pending",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=expiry)).isoformat(),
            "resolved_at": None,
        }
        with self._lock:
            payload = self._read_confirmations()
            payload["confirmations"].append(confirmation)
            payload["confirmations"] = payload["confirmations"][-200:]
            write_json(self.confirmations_path, payload)
        self.audit.log(
            "confirmation_requested",
            session_id=session_id,
            reason="Consequential action stopped by Tier 5 policy.",
            details={
                "confirmation_id": confirmation["id"],
                "action": action,
                "action_kind": action_kind,
                "description_character_count": len(confirmation["what_it_will_do"]),
            },
        )
        return public_confirmation(confirmation)

    def resolve_confirmation(self, confirmation_id: str, session_id: str, decision: str) -> dict:
        clean_decision = decision.casefold().strip()
        if clean_decision not in {"approve", "deny"}:
            raise ValueError("Decision must be approve or deny.")
        with self._lock:
            payload = self._read_confirmations()
            confirmation = next(
                (item for item in payload["confirmations"] if item.get("id") == confirmation_id),
                None,
            )
            if not confirmation or confirmation.get("session_id") != session_id:
                raise ValueError("No matching confirmation exists for this session.")
            if confirmation.get("status") != "pending":
                raise ValueError("This confirmation has already been resolved.")
            if parse_time(confirmation.get("expires_at")) < datetime.now(timezone.utc):
                confirmation["status"] = "expired"
                confirmation["resolved_at"] = datetime.now(timezone.utc).isoformat()
                write_json(self.confirmations_path, payload)
                raise ValueError("This confirmation expired. Request the action again.")
            confirmation["status"] = "approved" if clean_decision == "approve" else "denied"
            confirmation["resolved_at"] = datetime.now(timezone.utc).isoformat()
            write_json(self.confirmations_path, payload)
        self.audit.log(
            "confirmation_resolved",
            session_id=session_id,
            reason="User supplied a per-action confirmation decision.",
            details={
                "confirmation_id": confirmation_id,
                "action": confirmation["action"],
                "decision": confirmation["status"],
            },
        )
        return dict(confirmation)

    def pending_for_session(self, session_id: str) -> list[dict]:
        now = datetime.now(timezone.utc)
        return [
            public_confirmation(item)
            for item in self._read_confirmations()["confirmations"]
            if item.get("session_id") == session_id
            and item.get("status") == "pending"
            and parse_time(item.get("expires_at")) >= now
        ]

    def proactive_paused(self) -> bool:
        return bool(self._read_state().get("proactive_paused"))

    def set_proactive_paused(self, paused: bool, session_id: str | None = None) -> dict:
        with self._lock:
            state = self._read_state()
            state.update(
                {
                    "proactive_paused": bool(paused),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "updated_by_session": session_id,
                }
            )
            write_json(self.state_path, state)
        self.audit.log(
            "proactive_paused" if paused else "proactive_resumed",
            session_id=session_id,
            reason="User changed the global proactive-behavior kill switch.",
            details={"paused": bool(paused)},
        )
        return self.status()

    def status(self) -> dict:
        state = self._read_state()
        return {
            "proactive_paused": bool(state.get("proactive_paused")),
            "updated_at": state.get("updated_at"),
            "confirmation_required_actions": list(
                self.settings().get("confirmation_required_actions") or []
            ),
        }

    def settings(self) -> dict:
        return load_varyn_settings().get("safety") or {}

    def action_description(self, action: str, arguments: dict) -> str:
        descriptions = self.settings().get("action_descriptions") or {}
        template = descriptions.get(action) or f"Run consequential action: {action}."
        try:
            return template.format(**{key: str(value) for key, value in arguments.items()})
        except (KeyError, ValueError):
            return template

    def _read_state(self) -> dict:
        return read_json(self.state_path, default_state())

    def _read_confirmations(self) -> dict:
        return read_json(self.confirmations_path, {"version": 1, "confirmations": []})


def detect_instructional_content(text: str) -> list[dict]:
    settings = load_varyn_settings().get("safety") or {}
    patterns = settings.get("untrusted_instruction_patterns") or []
    flags = []
    for pattern in patterns:
        try:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        except re.error:
            continue
        if match:
            start = max(0, match.start() - 80)
            end = min(len(text), match.end() + 120)
            snippet = re.sub(r"\s+", " ", text[start:end]).strip()
            flags.append(
                {
                    "type": "instruction_like_content",
                    "pattern": pattern,
                    "snippet": snippet[:300],
                }
            )
    return flags[:5]


def public_confirmation(value: dict) -> dict:
    return {
        key: value.get(key)
        for key in (
            "id",
            "action",
            "action_kind",
            "what_it_will_do",
            "status",
            "created_at",
            "expires_at",
        )
    }


def default_state() -> dict:
    default_paused = bool(
        (load_varyn_settings().get("proactive") or {}).get("default_paused", False)
    )
    return {
        "version": 1,
        "proactive_paused": default_paused,
        "updated_at": None,
        "updated_by_session": None,
    }


def read_json(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return json.loads(json.dumps(fallback))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else json.loads(json.dumps(fallback))
    except (OSError, json.JSONDecodeError):
        return json.loads(json.dumps(fallback))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


_DEFAULT_RAILS = SafetyRails()


def get_safety_rails() -> SafetyRails:
    return _DEFAULT_RAILS
