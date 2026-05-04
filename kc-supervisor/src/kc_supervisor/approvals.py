from __future__ import annotations
import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ApprovalRequest:
    request_id: str
    agent: str
    tool: str
    arguments: dict[str, Any]


class _Subscription:
    """Handle returned by ApprovalBroker.subscribe(); call unsubscribe() to remove."""

    def __init__(
        self,
        broker: "ApprovalBroker",
        callback: Callable[[ApprovalRequest], None],
    ) -> None:
        self._broker = broker
        self.callback = callback

    def unsubscribe(self) -> None:
        self._broker._subs.discard(self)


class ApprovalBroker:
    """Async approval coordinator.

    Each ``request_approval()`` allocates a future and notifies all subscribers
    synchronously (subscriber callbacks must be cheap — typically they enqueue
    onto a WebSocket send queue and return immediately). Callers ``await`` the
    future; some other code path calls ``resolve()`` to fulfill it.

    ``resolve()`` is safe to call from any thread or event loop: the broker
    captures the awaiter's loop at ``request_approval()`` time and dispatches
    the result via ``loop.call_soon_threadsafe``.

    Subscriber exceptions are logged via ``logger.exception`` and otherwise
    swallowed — a misbehaving subscriber must not block an approval flow.
    """

    def __init__(self) -> None:
        # Each entry stores (future, owning_loop) so resolve() can fulfill the
        # future from any thread/loop via call_soon_threadsafe.
        self._futures: dict[
            str, tuple[asyncio.Future[tuple[bool, Optional[str]]], asyncio.AbstractEventLoop]
        ] = {}
        self._requests: dict[str, ApprovalRequest] = {}
        self._subs: set[_Subscription] = set()

    def subscribe(self, callback: Callable[[ApprovalRequest], None]) -> _Subscription:
        sub = _Subscription(broker=self, callback=callback)
        self._subs.add(sub)
        return sub

    async def request_approval(
        self, agent: str, tool: str, arguments: dict[str, Any],
    ) -> tuple[bool, Optional[str]]:
        request_id = uuid.uuid4().hex
        req = ApprovalRequest(
            request_id=request_id, agent=agent, tool=tool, arguments=arguments
        )
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._futures[request_id] = (fut, loop)
        self._requests[request_id] = req
        for sub in list(self._subs):
            try:
                sub.callback(req)
            except Exception:
                logger.exception("approval subscriber raised; ignoring")
        try:
            return await fut
        finally:
            self._futures.pop(request_id, None)
            self._requests.pop(request_id, None)

    def resolve(self, request_id: str, allowed: bool, reason: Optional[str]) -> None:
        """Fulfill an outstanding approval. Unknown request_ids are silently ignored.

        Safe to call from any thread or event loop — the result is dispatched
        to the loop that owns the future via ``call_soon_threadsafe``.
        """
        entry = self._futures.get(request_id)
        if entry is None:
            return
        fut, loop = entry
        if fut.done():
            return

        def _set_result():
            if not fut.done():
                fut.set_result((allowed, reason))

        try:
            loop.call_soon_threadsafe(_set_result)
        except RuntimeError:
            # Owning loop is closed — drop silently. The awaiter is gone too.
            return

    def pending(self) -> list[ApprovalRequest]:
        """Snapshot of currently-outstanding approval requests (for /ws/approvals reconnect)."""
        return list(self._requests.values())
