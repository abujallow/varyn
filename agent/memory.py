from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR


class LongTermMemoryStore:
    """Durable, user-auditable facts kept separate from session and file context."""

    def __init__(self, path: Path | None = None) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = path or DATA_DIR / "long_term_memory.json"
        self._lock = threading.RLock()
        if not self.path.exists():
            self._write({"version": 1, "facts": []})

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
            "path": str(self.path),
        }

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "facts": []}

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(
                "Durable memory could not be read. Correct long_term_memory.json and retry."
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
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(self.path)


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


class MemoryStore:
    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = DATA_DIR / "memory.json"
        self._lock = threading.RLock()
        self.data = self._load()

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

    def add_turn(self, session_id: str, role: str, content: str) -> None:
        with self._lock:
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
