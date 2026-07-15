import json
import os
import secrets
import threading
import time

from app.config import RUNTIME_DIR


class SessionStore:
    def __init__(self, ttl_seconds: int = 60 * 60 * 24 * 7):
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._path = os.path.join(os.fspath(RUNTIME_DIR), "sessions.json")
        self._sessions: dict[str, dict[str, float | str]] = self._load()

    # ------------------------------------------------------------------
    # File persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, dict[str, float | str]]:
        """Load sessions from disk, dropping expired entries."""
        if not os.path.exists(self._path):
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.loads(f.read().strip() or "{}")
        except (json.JSONDecodeError, OSError):
            return {}

        now = time.time()
        valid: dict[str, dict[str, float | str]] = {}
        for sid, data in raw.items():
            if not isinstance(data, dict):
                continue
            if float(data.get("expires_at", 0)) > now:
                valid[sid] = data
        return valid

    def _save(self) -> None:
        """Persist current sessions to disk."""
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._sessions, f, ensure_ascii=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create(self, username: str) -> str:
        session_id = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._sessions[session_id] = {
                "username": username,
                "created_at": now,
                "expires_at": now + self.ttl_seconds,
            }
            self._save()
        return session_id

    def get_username(self, session_id: str | None) -> str | None:
        if not session_id:
            return None
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None
            expires_at = float(session.get("expires_at", 0))
            if expires_at < time.time():
                self._sessions.pop(session_id, None)
                self._save()
                return None
            return str(session.get("username"))

    def delete(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._lock:
            if session_id in self._sessions:
                self._sessions.pop(session_id, None)
                self._save()


SESSION_STORE = SessionStore()
