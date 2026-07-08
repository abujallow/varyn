from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from config import DATA_DIR


class _LocalFileBackend:
    """Stores durable facts in a local JSON file. Used when no Upstash Redis is configured."""

    name = "local_file"

    def __init__(self, path: Path) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = path
        if not self.path.exists():
            self.write_raw(json.dumps({"version": 1, "facts": []}))

    def read_raw(self) -> str | None:
        if not self.path.exists():
            return None
        return self.path.read_text(encoding="utf-8")

    def write_raw(self, text: str) -> None:
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(self.path)

    def describe(self) -> str:
        return str(self.path)


class _UpstashBackend:
    """Stores durable facts as a single JSON value in Upstash Redis via its REST API.

    This survives Render restarts/redeploys, unlike the local filesystem, and reuses
    the same Upstash database already configured for Vercel's rate limiter.
    """

    name = "upstash_redis"

    def __init__(self, url: str, token: str, key: str = "varyn:long_term_memory") -> None:
        self._url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._key = key

    def read_raw(self) -> str | None:
        try:
            response = requests.get(f"{self._url}/get/{self._key}", headers=self._headers, timeout=5)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError("Durable memory (Upstash) could not be read.") from exc
        return response.json().get("result")

    def write_raw(self, text: str) -> None:
        try:
            response = requests.post(
                f"{self._url}/set/{self._key}",
                headers=self._headers,
                data=text.encode("utf-8"),
                timeout=5,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError("Durable memory (Upstash) could not be written.") from exc

    def describe(self) -> str:
        return f"upstash:{self._key}"


def _long_term_memory_backend(path: Path | None):
    url = os.getenv("KV_REST_API_URL", "").strip()
    token = os.getenv("KV_REST_API_TOKEN", "").strip()
    if url and token:
        return _UpstashBackend(url, token)
    return _LocalFileBackend(path or DATA_DIR / "long_term_memory.json")


class LongTermMemoryStore:
    """Durable, user-auditable facts kept separate from session and file context.

    Backed by Upstash Redis when KV_REST_API_URL/KV_REST_API_TOKEN are present (hosted),
    otherwise a local JSON file (local dev) — same interface either way.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._backend = _long_term_memory_backend(path)
        self._lock = threading.RLock()

    def list_facts(self) -> list[dict]:
        with self._lock:
            return list(self._read()["facts"])

    def relevant_facts(self, query: str, limit: int = 6) -> list[dict]:
        facts = self.list_facts()
        if not facts:
            return []

        lowered = query.casefold()
        if any(phrase in lowered for phrase in ("what do you remember", "what do you know about me", "list my preferences")):
            return facts[:limit]

        query_tokens = memory_tokens(query)
        if not query_tokens:
            return []

        ranked = []
        for position, fact in enumerate(facts):
            statement_tokens = memory_tokens(fact.get("statement", ""))
            overlap = query_tokens.intersection(statement_tokens)
            if not overlap:
                continue
            score = len(overlap) / max(1, len(query_tokens))
            ranked.append((score, -position, fact))

        ranked.sort(reverse=True, key=lambda item: (item[0], item[1]))
        return [fact for _score, _position, fact in ranked[:limit]]

    def remember(self, statement: str) -> dict:
        clean_statement = normalize_fact(statement)
        with self._lock:
            data = self._read()
            for fact in data["facts"]:
                if fact["statement"].casefold() == clean_statement.casefold():
                    return {"created": False, "fact": fact}

            now = datetime.now(timezone.utc).isoformat()
            fact = {
                "id": f"fact-{uuid.uuid4().hex[:10]}",
                "statement": clean_statement,
                "created_at": now,
                "updated_at": now,
            }
            data["facts"].append(fact)
            self._write(data)
            return {"created": True, "fact": fact}

    def update(self, fact_id: str, statement: str) -> dict:
        clean_statement = normalize_fact(statement)
        with self._lock:
            data = self._read()
            for fact in data["facts"]:
                if fact["id"] == fact_id:
                    fact["statement"] = clean_statement
                    fact["updated_at"] = datetime.now(timezone.utc).isoformat()
                    self._write(data)
                    return {"updated": True, "fact": fact}
        raise ValueError(f"No durable fact exists with id {fact_id}.")

    def forget(self, fact_id: str) -> dict:
        with self._lock:
            data = self._read()
            for index, fact in enumerate(data["facts"]):
                if fact["id"] == fact_id:
                    removed = data["facts"].pop(index)
                    self._write(data)
                    return {"forgotten": True, "fact": removed}
        raise ValueError(f"No durable fact exists with id {fact_id}.")

    def summary(self) -> dict:
        facts = self.list_facts()
        return {
            "active": True,
            "fact_count": len(facts),
            "backend": self._backend.name,
            "durable": True,
            "path": self._backend.describe(),
        }

    def _read(self) -> dict:
        raw_text = self._backend.read_raw()
        if not raw_text:
            return {"version": 1, "facts": []}

        try:
            raw = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Durable memory could not be read. Correct the stored value and retry."
            ) from exc

        if isinstance(raw, list):
            raw = {"version": 1, "facts": raw}
        if not isinstance(raw, dict) or not isinstance(raw.get("facts"), list):
            raise RuntimeError("Durable memory must contain a JSON object with a facts list.")

        facts = []
        for entry in raw["facts"]:
            if not isinstance(entry, dict):
                continue
            fact_id = str(entry.get("id", "")).strip()
            statement = str(entry.get("statement", "")).strip()
            if fact_id and statement:
                facts.append(
                    {
                        "id": fact_id,
                        "statement": normalize_fact(statement),
                        "created_at": entry.get("created_at"),
                        "updated_at": entry.get("updated_at"),
                    }
                )
        return {"version": 1, "facts": facts}

    def _write(self, data: dict) -> None:
        self._backend.write_raw(json.dumps(data, indent=2, ensure_ascii=True) + "\n")


def normalize_fact(statement: str) -> str:
    cleaned = re.sub(r"\s+", " ", statement).strip()
    if not cleaned:
        raise ValueError("A durable fact cannot be empty.")
    if len(cleaned) > 500:
        raise ValueError("A durable fact must be 500 characters or fewer.")
    return cleaned


MEMORY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "do",
    "for",
    "i",
    "in",
    "is",
    "me",
    "my",
    "of",
    "on",
    "the",
    "to",
    "what",
    "you",
}


def memory_tokens(text: str) -> set[str]:
    return {
        MEMORY_TOKEN_ALIASES.get(token, token)
        for token in re.findall(r"[a-zA-Z0-9&]+", text.casefold())
        if len(token) > 1 and token not in MEMORY_STOPWORDS
    }


MEMORY_TOKEN_ALIASES = {
    "briefings": "briefing",
    "preferences": "preference",
    "watched": "watch",
    "watches": "watch",
    "watching": "watch",
    "watchlist": "watch",
    "watchlists": "watch",
}


SESSION_TTL_HOURS = float(os.getenv("VARYN_SESSION_TTL_HOURS", "48"))
SESSION_PRUNE_INTERVAL_SECONDS = 3600


class MemoryStore:
    """Per-session chat memory. Intentionally ephemeral — sessions older than
    SESSION_TTL_HOURS are pruned so a public demo doesn't accumulate unbounded
    session entries between restarts."""

    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = DATA_DIR / "memory.json"
        self._lock = threading.RLock()
        self._last_prune_monotonic = 0.0
        self.data = self._load()
        self._prune_stale_sessions_locked()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"sessions": {}, "preferences": {}, "project_context": [], "files": {}}

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            data.setdefault("files", {})
            return data
        except json.JSONDecodeError:
            return {"sessions": {}, "preferences": {}, "project_context": [], "files": {}}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    @staticmethod
    def _is_stale(timestamp: str | None, cutoff: datetime) -> bool:
        if not timestamp:
            return True
        try:
            parsed = datetime.fromisoformat(timestamp)
        except (TypeError, ValueError):
            return True
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed < cutoff

    def _prune_stale_sessions_locked(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=SESSION_TTL_HOURS)
        sessions = self.data.setdefault("sessions", {})
        files = self.data.setdefault("files", {})

        stale_sessions = [
            session_id
            for session_id, turns in sessions.items()
            if self._is_stale(turns[-1].get("timestamp") if turns else None, cutoff)
        ]
        for session_id in stale_sessions:
            sessions.pop(session_id, None)

        stale_files = [
            session_id
            for session_id, context in files.items()
            if session_id not in sessions and self._is_stale(context.get("loaded_at"), cutoff)
        ]
        for session_id in stale_files:
            files.pop(session_id, None)

        if stale_sessions or stale_files:
            self._save()

    def _maybe_prune_stale_sessions(self) -> None:
        now = time.monotonic()
        if now - self._last_prune_monotonic < SESSION_PRUNE_INTERVAL_SECONDS:
            return
        self._last_prune_monotonic = now
        self._prune_stale_sessions_locked()

    def add_turn(self, session_id: str, role: str, content: str) -> None:
        with self._lock:
            self._maybe_prune_stale_sessions()
            sessions = self.data.setdefault("sessions", {})
            session = sessions.setdefault(session_id, [])
            session.append(
                {
                    "role": role,
                    "content": content,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            del session[:-20]
            self._capture_preference(content)
            self._save()

    def recent_context(self, session_id: str) -> list[dict]:
        with self._lock:
            return list(self.data.get("sessions", {}).get(session_id, [])[-10:])

    def session_summary(self, session_id: str) -> dict:
        with self._lock:
            active_file = self.data.get("files", {}).get(session_id)
            return {
                "turns": len(self.data.get("sessions", {}).get(session_id, [])),
                "preferences": dict(self.data.get("preferences", {})),
                "active_file": summarize_file_context(active_file) if active_file else None,
            }

    def set_file_context(self, session_id: str, file_context: dict) -> None:
        with self._lock:
            files = self.data.setdefault("files", {})
            files[session_id] = {
                **file_context,
                "loaded_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save()

    def get_file_context(self, session_id: str) -> dict | None:
        with self._lock:
            context = self.data.get("files", {}).get(session_id)
            return dict(context) if context else None

    def clear_file_context(self, session_id: str) -> None:
        with self._lock:
            self.data.setdefault("files", {}).pop(session_id, None)
            self._save()

    def reset_session(self, session_id: str) -> None:
        with self._lock:
            self.data.setdefault("sessions", {}).pop(session_id, None)
            self.data.setdefault("files", {}).pop(session_id, None)
            self._save()

    def _capture_preference(self, content: str) -> None:
        lowered = content.lower()
        if "my name is " in lowered:
            name = content[lowered.index("my name is ") + len("my name is ") :].strip().split(".")[0]
            if name:
                self.data.setdefault("preferences", {})["name"] = name[:80]

        if "call me " in lowered:
            name = content[lowered.index("call me ") + len("call me ") :].strip().split(".")[0]
            if name:
                self.data.setdefault("preferences", {})["preferred_name"] = name[:80]


def summarize_file_context(file_context: dict | None) -> dict | None:
    if not file_context:
        return None

    return {
        "name": file_context.get("name"),
        "size": file_context.get("size"),
        "extension": file_context.get("extension"),
        "ready": file_context.get("ready"),
        "extraction_status": file_context.get("extraction_status"),
        "extracted_chars": file_context.get("extracted_chars"),
        "loaded_at": file_context.get("loaded_at"),
    }
