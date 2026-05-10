from __future__ import annotations
import logging
import time
from typing import Any, Callable, Coroutine, Optional

from kc_core.messages import AssistantMessage
from kc_supervisor.scheduling.context import set_current_context
from kc_supervisor.storage import Storage


logger = logging.getLogger(__name__)
PREFIX = "⏰ "


def _filter_tools(tools: Any, *, exclude: set[str]) -> Any:
    """Return a tool registry-like view that hides tools whose names are in `exclude`.

    The kc-core agent reads tools via `.to_openai_schema()`, `.names()`, and
    `.invoke(name, args)`. The proxy delegates each, filtering by name.
    """
    class _Filtered:
        def __init__(self, inner: Any, blocked: set[str]) -> None:
            self._inner = inner
            self._blocked = blocked
        def names(self) -> list[str]:
            return [n for n in self._inner.names() if n not in self._blocked]
        def to_openai_schema(self) -> list:
            return [
                t for t in self._inner.to_openai_schema()
                if (t.get("function", {}).get("name") if isinstance(t, dict) else None) not in self._blocked
            ]
        def invoke(self, name: str, args: dict) -> Any:
            if name in self._blocked:
                raise ValueError(f"tool {name!r} is unavailable in this context")
            return self._inner.invoke(name, args)
    return _Filtered(tools, exclude)


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

    _AGENT_PHRASED_ADDENDUM = (
        "\n\nYou are responding to a scheduled reminder you set for the user. "
        "The trigger description follows. Compose a single friendly message — "
        "do not mention this is a reminder fire unless the trigger asks you to."
    )
    _STRIPPED_TOOL_NAMES = {"schedule_reminder", "schedule_cron", "cancel_reminder"}

    def __init__(
        self,
        *,
        storage: Storage,
        conversations: Any,        # ConversationManager
        connector_registry: Any,   # ConnectorRegistry
        coroutine_runner: CoroRunner,
        agent_registry: Optional[Any] = None,  # AgentRegistry; required for mode='agent_phrased'
    ) -> None:
        self.storage = storage
        self.conversations = conversations
        self.connector_registry = connector_registry
        self._run_coro = coroutine_runner
        self.agent_registry = agent_registry

    def fire(self, job_id: int) -> None:
        row = self.storage.get_scheduled_job(job_id)
        if row is None:
            logger.warning("ReminderRunner.fire: job %s not found; skipping", job_id)
            return

        # Resolve destination conversation. For same-channel rows this returns
        # row["conversation_id"]; for cross-channel rows, the conversation
        # belonging to the destination chat (created on demand).
        try:
            dest_conv_id = self.conversations.get_or_create(
                channel=row["channel"], chat_id=row["chat_id"], agent=row["agent"],
            )
        except Exception:
            logger.exception(
                "ReminderRunner.fire: get_or_create destination failed for job %s", job_id,
            )
            self.storage.update_scheduled_job_after_fire(
                job_id, fired_at=time.time(), new_status="failed",
            )
            return

        text = PREFIX + (row["payload"] or "")  # mode branch added in Task 6.3

        if row["channel"] == "dashboard":
            try:
                self.conversations.append(
                    dest_conv_id, AssistantMessage(content=text),
                )
            except Exception:
                logger.exception(
                    "ReminderRunner.fire: dashboard persist failed for job %s", job_id,
                )
                self.storage.update_scheduled_job_after_fire(
                    job_id, fired_at=time.time(), new_status="failed",
                )
                return
            new_status = "done" if row["kind"] == "reminder" else "pending"
            self.storage.update_scheduled_job_after_fire(
                job_id, fired_at=time.time(), new_status=new_status,
            )
            return

        try:
            connector = self.connector_registry.get(row["channel"])
            self._run_coro(connector.send(row["chat_id"], text))
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
                dest_conv_id, AssistantMessage(content=text),
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

    def _compose_agent_phrased(self, row: dict, *, dest_conv_id: int) -> Optional[str]:
        """Run a fire-time agent turn for an agent_phrased row. Returns the agent's
        final assistant text, or None on failure (caller marks the row failed).

        NOTE on concurrency: this method mutates `core.tools` and `core.system_prompt`
        on the shared AssembledAgent.core_agent instance for the duration of the turn,
        and restores them in `finally`. The supervisor serializes per-conversation
        agent turns via conv_locks (see ws_routes / inbound), but the APScheduler
        worker thread that calls this method does NOT participate in that lock. If
        a normal user turn for the same agent is in-flight when this fires, the
        tool/prompt mutations could race. Acceptable risk for Phase 2 (single user,
        rare overlap); revisit if the agent registry grows or concurrency becomes
        observable.
        """
        if self.agent_registry is None:
            logger.error(
                "agent_phrased fire requires agent_registry (job %s)", row["id"],
            )
            return None
        runtime = self.agent_registry.get(row["agent"])
        if runtime is None or runtime.assembled is None:
            logger.error(
                "agent_phrased fire: agent %s not found / degraded (job %s)",
                row["agent"], row["id"],
            )
            return None

        core = runtime.assembled.core_agent
        original_tools = core.tools
        original_system_prompt = core.system_prompt
        try:
            history = self.conversations.list_messages(dest_conv_id)
            core.history = list(history)
            core.system_prompt = (
                runtime.assembled.base_system_prompt + self._AGENT_PHRASED_ADDENDUM
            )
            core.tools = _filter_tools(original_tools, exclude=self._STRIPPED_TOOL_NAMES)

            set_current_context({
                "conversation_id": dest_conv_id,
                "channel": row["channel"],
                "chat_id": row["chat_id"],
                "agent": row["agent"],
            })

            scheduled_iso = (
                f"{row['when_utc']}" if row["when_utc"] else (row["cron_spec"] or "?")
            )
            now_iso = f"{time.time():.0f}"
            trigger = (
                f"[Internal trigger — scheduled at {scheduled_iso}, "
                f"fired at {now_iso}] {row['payload']}"
            )

            async def _run() -> Optional[str]:
                final_text: Optional[str] = None
                async for frame in core.send_stream(trigger):
                    reply = getattr(frame, "reply", None)
                    if isinstance(reply, AssistantMessage):
                        final_text = reply.content
                return final_text

            try:
                reply_text = self._run_coro(_run())
            except Exception:
                logger.exception(
                    "agent_phrased turn raised for job %s", row["id"],
                )
                return None

            if not reply_text or not reply_text.strip():
                logger.warning(
                    "agent_phrased turn returned empty text for job %s", row["id"],
                )
                return None
            return reply_text
        finally:
            core.tools = original_tools
            core.system_prompt = original_system_prompt


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
