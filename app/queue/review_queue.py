"""Human review queue for flagged conversations.

Stores items in an in-memory async queue. In production, replace with
a Redis list, SQS queue, or database table.
"""
import asyncio
from datetime import datetime, timezone
from typing import Any

class InMemoryReviewQueue:
    """Async review queue backed by asyncio.Queue."""

    def __init__(self, maxsize: int = 1000):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._all_items: list[dict] = []  # For admin visibility
        self._lock = asyncio.Lock()

    async def enqueue(self, item: dict[str, Any]) -> None:
        """Add an item to the review queue."""
        enriched = {**item, "queued_at": datetime.now(timezone.utc).isoformat()}
        await self._queue.put(enriched)
        async with self._lock:
            self._all_items.append(enriched)

    async def dequeue(self) -> dict | None:
        """Non-blocking dequeue. Returns None if empty."""
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def list_pending(self, limit: int = 50) -> list[dict]:
        """Return the most recent pending items (admin view)."""
        async with self._lock:
            return self._all_items[-limit:]

    async def size(self) -> int:
        return self._queue.qsize()

# Singleton
_queue: InMemoryReviewQueue | None = None

def get_review_queue() -> InMemoryReviewQueue:
    global _queue
    if _queue is None:
        _queue = InMemoryReviewQueue()
    return _queue
