"""Session data models."""
from dataclasses import dataclass, field
from datetime import datetime, timezone

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

@dataclass
class ConversationTurn:
    user: str
    assistant: str
    intent: str = "general"
    agent: str = "general_agent"
    timestamp: str = field(default_factory=_utcnow)

@dataclass
class ConversationSession:
    session_id: str
    turns: list[ConversationTurn] = field(default_factory=list)
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    customer_id: str | None = None
