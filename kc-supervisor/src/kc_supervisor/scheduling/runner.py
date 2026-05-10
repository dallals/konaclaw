from __future__ import annotations
import logging
import time
from typing import Any, Callable, Coroutine, Optional

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


# ---- Module-level fire callable ----
#
# APScheduler's SQLAlchemyJobStore pickles each job's trigger callable. A bound
# method like `runner.fire` packs the runner instance into the job's args so
# rehydration can re-invoke `getattr(args[0], 'fire')(...)`. That breaks in
# production because the runner holds a TelegramConnector with a running
# aiohttp ClientSession (not picklable).
#
# Workaround: store the active runner on a module global and pass APS a
# module-level function reference. APS then only pickles the function name
# (`kc_supervisor.scheduling.runner:fire_reminder`) — the runner instance
# stays in-process.

_runner: Optional[ReminderRunner] = None


def set_active_runner(runner: ReminderRunner) -> None:
    """Register the process-wide active ReminderRunner. Called from the
    supervisor entrypoint after the runner is constructed, and from tests
    that need the module-level `fire_reminder` to dispatch to a specific
    runner instance.
    """
    global _runner
    _runner = runner


def clear_active_runner() -> None:
    """Clear the module-level active runner. Used by test teardown."""
    global _runner
    _runner = None


def fire_reminder(job_id: int) -> None:
    """Module-level fire callable for APScheduler. Dispatches to the active
    ReminderRunner registered via `set_active_runner`. If no runner is
    registered (e.g., a job fires after shutdown), logs a warning and
    returns.
    """
    if _runner is None:
        logger.warning(
            "fire_reminder: no active ReminderRunner registered; job %s skipped",
            job_id,
        )
        return
    _runner.fire(job_id)
