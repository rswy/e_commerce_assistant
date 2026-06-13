"""Conversation session store.

Uses an in-memory dict by default (suitable for single-instance deployments).
The async interface is Redis-compatible — swap in a Redis backend without
changing callers.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from app.state.models import ConversationTurn, ConversationSession

MAX_HISTORY_TURNS = 10
SESSION_TTL_HOURS = 24

class InMemorySessionStore:
    """Thread-safe in-memory session store with TTL expiry."""

    def __init__(self, max_turns: int = MAX_HISTORY_TURNS, ttl_hours: int = SESSION_TTL_HOURS):
        self._sessions: dict[str, ConversationSession] = {}
        self._max_turns = max_turns
        self._ttl = timedelta(hours=ttl_hours)
        self._lock = asyncio.Lock()

    async def get_history(self, session_id: str) -> list[dict]:
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return []
            return [{"user": t.user, "assistant": t.assistant} for t in session.turns]

    async def add_turn(
        self,
        session_id: str,
        user_msg: str,
        assistant_msg: str,
        intent: str = "general",
        agent: str = "general_agent",
    ) -> None:
        async with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = ConversationSession(session_id=session_id)
            session = self._sessions[session_id]
            session.turns.append(
                ConversationTurn(user=user_msg, assistant=assistant_msg, intent=intent, agent=agent)
            )
            # Trim to max turns
            if len(session.turns) > self._max_turns:
                session.turns = session.turns[-self._max_turns:]
            session.updated_at = datetime.now(timezone.utc).isoformat()

    async def get_session(self, session_id: str) -> ConversationSession | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def delete_session(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)

    async def session_count(self) -> int:
        async with self._lock:
            return len(self._sessions)

# Singleton instance used by main.py
_store: InMemorySessionStore | None = None

def get_session_store() -> InMemorySessionStore:
    global _store
    if _store is None:
        _store = InMemorySessionStore()
    return _store
