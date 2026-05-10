from __future__ import annotations
import logging
import time
from typing import Any, Callable, Coroutine

from kc_core.messages import AssistantMessage
from kc_supervisor.storage import Storage


logger = logging.getLogger(__name__)
PREFIX = "⏰ "

CoroRunner = Callable[[Coroutine], Any]


class ReminderRunner:
    """APScheduler trigger callback. Sends a reminder via the connector and
    persists the AssistantMessage row.

    `coroutine_runner` is a callable that takes a coroutine and runs it to
    completion synchronously. Production wiring passes a lambda that bridges
    to the FastAPI event loop via `asyncio.run_coroutine_threadsafe(..., loop)`
    (because APS triggers run in a worker thread, not the event loop). Tests
    pass `lambda c: asyncio.run(c)` directly.
    """

    def __init__(
        self,
        *,
        storage: Storage,
        conversations: Any,        # ConversationManager
        connector_registry: Any,   # ConnectorRegistry
        coroutine_runner: CoroRunner,
    ) -> None:
        self.storage = storage
        self.conversations = conversations
        self.connector_registry = connector_registry
        self._run_coro = coroutine_runner

    def fire(self, job_id: int) -> None:
        row = self.storage.get_scheduled_job(job_id)
        if row is None:
            logger.warning("ReminderRunner.fire: job %s not found; skipping", job_id)
            return
        prefixed = PREFIX + (row["payload"] or "")
        try:
            connector = self.connector_registry.get(row["channel"])
            self._run_coro(connector.send(row["chat_id"], prefixed))
        except Exception:
            logger.exception(
                "ReminderRunner.fire: connector send failed for job %s", job_id,
            )
            self.storage.update_scheduled_job_after_fire(
                job_id, fired_at=time.time(), new_status="failed",
            )
            return
        try:
            self.conversations.append(
                row["conversation_id"], AssistantMessage(content=prefixed),
            )
        except Exception:
            logger.exception(
                "ReminderRunner.fire: persist failed for job %s; user already received message",
                job_id,
            )
        new_status = "done" if row["kind"] == "reminder" else "pending"
        self.storage.update_scheduled_job_after_fire(
            job_id, fired_at=time.time(), new_status=new_status,
        )
