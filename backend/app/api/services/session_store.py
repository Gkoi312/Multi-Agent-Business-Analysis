import secrets
import threading
import time


class SessionStore:
    def __init__(self, ttl_seconds: int = 60 * 60 * 24 * 7):
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, float | str]] = {}

    def create(self, username: str) -> str:
        session_id = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._sessions[session_id] = {
                "username": username,
                "created_at": now,
                "expires_at": now + self.ttl_seconds,
            }
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
                return None
            return str(session.get("username"))

    def delete(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)


SESSION_STORE = SessionStore()
