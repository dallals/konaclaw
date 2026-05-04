from __future__ import annotations
import asyncio


class ConversationLocks:
    """Lazy per-conversation-id asyncio locks.

    Locks are created on first access and never evicted. For a single-user local
    app with finite conversations this is fine; switch to ``WeakValueDictionary``
    if multi-user support ever lands.
    """

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}

    def get(self, cid: int) -> asyncio.Lock:
        """Return the lock for ``cid``, creating it on first call."""
        lock = self._locks.get(cid)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[cid] = lock
        return lock
