from __future__ import annotations
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class _PendingClarify:
    request_id:      str
    conversation_id: int
    agent:           str
    question:        str
    choices:         list[str]
    started_at:      float
    timeout_seconds: int
    future:          asyncio.Future = field(repr=False)
    loop:            asyncio.AbstractEventLoop = field(repr=False)


class ClarifyBroker:
    """In-memory broker for pending clarification requests.

    Mirrors ApprovalBroker — request_clarification() allocates a future, calls
    subscribers synchronously with a clarify_request frame, then awaits the
    future via asyncio.wait_for(timeout=...). The WS handler calls resolve()
    when the user clicks a choice or Skip.
    """

    def __init__(self) -> None:
        self._pending: dict[str, _PendingClarify] = {}
        self._subscribers: list[Callable[[dict], None]] = []

    def subscribe(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        self._subscribers.append(fn)

        def unsubscribe() -> None:
            try:
                self._subscribers.remove(fn)
            except ValueError:
                pass

        return unsubscribe

    async def request_clarification(
        self,
        *,
        conversation_id: int,
        agent: str,
        question: str,
        choices: list[str],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        started = time.time()
        pending = _PendingClarify(
            request_id=request_id,
            conversation_id=conversation_id,
            agent=agent,
            question=question,
            choices=list(choices),
            started_at=started,
            timeout_seconds=timeout_seconds,
            future=fut,
            loop=loop,
        )
        self._pending[request_id] = pending

        frame = {
            "type":             "clarify_request",
            "request_id":       request_id,
            "conversation_id":  conversation_id,
            "agent":            agent,
            "question":         question,
            "choices":          list(choices),
            "timeout_seconds":  timeout_seconds,
            "started_at":       started,
        }
        for sub in list(self._subscribers):
            try:
                sub(frame)
            except Exception:
                logger.exception("clarify subscriber raised; ignoring")

        try:
            return await asyncio.wait_for(fut, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            elapsed_ms = int((time.time() - started) * 1000)
            return {"choice": None, "reason": "timeout", "elapsed_ms": elapsed_ms}
        finally:
            self._pending.pop(request_id, None)

    def resolve(
        self,
        request_id: str,
        *,
        choice: Optional[str],
        reason: str = "answered",
    ) -> None:
        """Fulfill an outstanding clarify request. Unknown ids and
        already-resolved ids are silently ignored."""
        pending = self._pending.get(request_id)
        if pending is None:
            return
        fut, loop = pending.future, pending.loop
        if fut.done():
            return

        if choice is None:
            payload: dict[str, Any] = {"choice": None, "reason": reason}
        else:
            try:
                idx = pending.choices.index(choice)
            except ValueError:
                idx = -1
            elapsed_ms = int((time.time() - pending.started_at) * 1000)
            payload = {"choice": choice, "choice_index": idx, "elapsed_ms": elapsed_ms}

        def _set() -> None:
            if not fut.done():
                fut.set_result(payload)

        try:
            loop.call_soon_threadsafe(_set)
        except RuntimeError:
            # Loop closed — drop silently.
            return

    def pending_for_conversation(self, conversation_id: int) -> list[dict[str, Any]]:
        """Snapshot of currently-outstanding requests for one conversation
        (for /ws/chat/{N} reconnect handlers)."""
        out = []
        for p in self._pending.values():
            if p.conversation_id != conversation_id:
                continue
            out.append({
                "type":             "clarify_request",
                "request_id":       p.request_id,
                "conversation_id":  p.conversation_id,
                "agent":            p.agent,
                "question":         p.question,
                "choices":          list(p.choices),
                "timeout_seconds":  p.timeout_seconds,
                "started_at":       p.started_at,
            })
        return out
