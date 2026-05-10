from __future__ import annotations
import asyncio
from datetime import datetime, timezone as _tz_mod
from pathlib import Path
from typing import Any, Optional, Protocol
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger

from croniter import croniter
from cron_descriptor import get_description

from kc_supervisor.storage import Storage
from kc_supervisor.scheduling.runner import fire_reminder
from kc_supervisor.scheduling.timeparse import parse_when, is_past, humanize


logger = logging.getLogger(__name__)
MAX_PAYLOAD_CHARS = 4000


class _RunnerLike(Protocol):
    """Anything with a `fire(job_id: int)` callable. Decoupled so tests can mock."""
    def fire(self, job_id: int) -> None: ...


class ScheduleService:
    """High-level scheduler API.

    Wraps APScheduler with SQLAlchemyJobStore over the same SQLite file as
    application data. The `scheduled_jobs` table (managed by Storage) is the
    human-readable mirror; APS's internal tables are implementation detail.
    """

    def __init__(
        self,
        storage: Storage,
        runner: _RunnerLike,
        db_path: Path,
        timezone: str,
    ) -> None:
        self.storage = storage
        self.runner = runner
        self._tz = timezone
        sqlalchemy_url = f"sqlite:///{db_path}"
        self._scheduler = AsyncIOScheduler(
            jobstores={
                "default": SQLAlchemyJobStore(url=sqlalchemy_url),
                "_internal": MemoryJobStore(),
            },
            timezone=timezone,
        )

    def start(self) -> None:
        if not self._scheduler.running:
            # AsyncIOScheduler.start() calls asyncio.get_running_loop(), which
            # fails in sync contexts (notably tests). Supply a loop explicitly.
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                self._scheduler._eventloop = loop
            self._scheduler.start()
        # Initial reconcile + 60-second background tick (memory jobstore so
        # the bound method isn't pickled into the SQLAlchemy jobstore).
        self.reconcile()
        self._scheduler.add_job(
            self.reconcile, trigger="interval", seconds=60,
            id="__reconcile__", replace_existing=True,
            jobstore="_internal",
        )

    def shutdown(self) -> None:
        if self._scheduler.running:
            try:
                self._scheduler.remove_job("__reconcile__")
            except Exception:
                pass
            self._scheduler.shutdown(wait=False)

    # ---- one-shot ----

    _ALLOWED_TARGET_CHANNELS = {"current", "telegram", "dashboard", "imessage"}
    _ALLOWED_MODES = {"literal", "agent_phrased"}
    _ALLOWED_SCOPES = {"user", "conversation"}

    def schedule_one_shot(
        self,
        *,
        when: str,
        content: str,
        conversation_id: int,
        channel: str,
        chat_id: str,
        agent: str,
        target_channel: str = "current",
        mode: str = "literal",
    ) -> dict:
        if not content or not content.strip():
            raise ValueError("content must be 1-4000 chars")
        if len(content) > MAX_PAYLOAD_CHARS:
            raise ValueError(f"content must be 1-{MAX_PAYLOAD_CHARS} chars")
        if mode not in self._ALLOWED_MODES:
            raise ValueError(f"unknown mode {mode!r}")
        if target_channel not in self._ALLOWED_TARGET_CHANNELS:
            raise ValueError(f"unknown channel {target_channel!r}")

        if target_channel == "current":
            use_channel, use_chat_id = channel, chat_id
        else:
            routing = self.storage.get_channel_routing(target_channel)
            if routing is None:
                raise ValueError(f"channel {target_channel!r} not configured (no routing entry)")
            if not routing["enabled"]:
                raise ValueError(f"channel {target_channel!r} is disabled")
            use_channel, use_chat_id = target_channel, routing["default_chat_id"]

        target = parse_when(when, self._tz)
        if is_past(target):
            raise ValueError(f"'when' resolves to the past: {when!r}")
        target_utc = target.astimezone(_tz_mod.utc)

        job_id = self.storage.add_scheduled_job(
            kind="reminder", agent=agent, conversation_id=conversation_id,
            channel=use_channel, chat_id=use_chat_id, payload=content,
            when_utc=target_utc.timestamp(), cron_spec=None,
            mode=mode,
        )
        try:
            self._scheduler.add_job(
                fire_reminder, trigger=DateTrigger(run_date=target),
                kwargs={"job_id": job_id}, id=str(job_id),
                misfire_grace_time=86400,
                replace_existing=True,
            )
        except Exception:
            self.storage.delete_scheduled_job(job_id)
            raise

        return {
            "id": job_id,
            "fires_at": target.isoformat(),
            "fires_at_human": humanize(target),
            "kind": "reminder",
        }

    # ---- cron ----

    def schedule_cron(
        self,
        *,
        cron: str,
        content: str,
        conversation_id: int,
        channel: str,
        chat_id: str,
        agent: str,
        target_channel: str = "current",
        mode: str = "literal",
    ) -> dict:
        if not content or not content.strip():
            raise ValueError("content must be 1-4000 chars")
        if len(content) > MAX_PAYLOAD_CHARS:
            raise ValueError(f"content must be 1-{MAX_PAYLOAD_CHARS} chars")
        if mode not in self._ALLOWED_MODES:
            raise ValueError(f"unknown mode {mode!r}")
        if target_channel not in self._ALLOWED_TARGET_CHANNELS:
            raise ValueError(f"unknown channel {target_channel!r}")
        if not croniter.is_valid(cron):
            raise ValueError(f"invalid cron: {cron!r}")

        if target_channel == "current":
            use_channel, use_chat_id = channel, chat_id
        else:
            routing = self.storage.get_channel_routing(target_channel)
            if routing is None:
                raise ValueError(f"channel {target_channel!r} not configured (no routing entry)")
            if not routing["enabled"]:
                raise ValueError(f"channel {target_channel!r} is disabled")
            use_channel, use_chat_id = target_channel, routing["default_chat_id"]

        try:
            trigger = CronTrigger.from_crontab(cron, timezone=self._tz)
        except ValueError as e:
            raise ValueError(f"invalid cron: {cron!r} ({e})")

        next_fire = trigger.get_next_fire_time(None, datetime.now(_tz_mod.utc))
        try:
            human_summary = get_description(cron)
        except Exception:
            human_summary = cron

        job_id = self.storage.add_scheduled_job(
            kind="cron", agent=agent, conversation_id=conversation_id,
            channel=use_channel, chat_id=use_chat_id, payload=content,
            when_utc=None, cron_spec=cron,
            mode=mode,
        )
        try:
            self._scheduler.add_job(
                fire_reminder, trigger=trigger,
                kwargs={"job_id": job_id}, id=str(job_id),
                coalesce=True,
                replace_existing=True,
            )
        except Exception:
            self.storage.delete_scheduled_job(job_id)
            raise

        return {
            "id": job_id,
            "next_fire": next_fire.isoformat() if next_fire else None,
            "next_fire_human": humanize(next_fire) if next_fire else None,
            "human_summary": human_summary,
            "kind": "cron",
        }

    # ---- list / cancel ----

    def list_reminders(
        self, *, conversation_id: int, active_only: bool = True,
        scope: str = "user",
    ) -> dict:
        if scope not in self._ALLOWED_SCOPES:
            raise ValueError(f"unknown scope {scope!r}")
        statuses = ("pending",) if active_only else None
        if scope == "conversation":
            rows = self.storage.list_scheduled_jobs(
                conversation_id=conversation_id, statuses=statuses,
            )
        else:
            rows = self.storage.list_scheduled_jobs(statuses=statuses)
        return {"reminders": [self._row_to_view(r) for r in rows]}

    def cancel_reminder(
        self, id_or_description: str, *, conversation_id: int,
        scope: str = "user",
    ) -> dict:
        if not id_or_description:
            raise ValueError("id_or_description must not be empty")
        if scope not in self._ALLOWED_SCOPES:
            raise ValueError(f"unknown scope {scope!r}")

        if scope == "conversation":
            candidates = self.storage.list_scheduled_jobs(
                conversation_id=conversation_id, statuses=("pending",),
            )
        else:
            candidates = self.storage.list_scheduled_jobs(statuses=("pending",))

        if id_or_description.strip().isdigit():
            target_id = int(id_or_description)
            matches = [r for r in candidates if r["id"] == target_id]
            if not matches:
                raise ValueError(f"no reminder with id {target_id}")
            return self._do_cancel(matches)

        needle = id_or_description.lower()
        matches = [r for r in candidates if needle in (r["payload"] or "").lower()]
        if not matches:
            raise ValueError(f"no reminder matched {id_or_description!r}")
        if len(matches) > 1:
            return {
                "ambiguous": True,
                "candidates": [
                    {"id": r["id"], "content": r["payload"]} for r in matches
                ],
                "cancelled": [],
            }
        return self._do_cancel(matches)

    def _do_cancel(self, rows: list[dict]) -> dict:
        cancelled: list[dict] = []
        for r in rows:
            try:
                self._scheduler.remove_job(str(r["id"]))
            except Exception:
                logger.debug("APS job %s not found; updating DB row anyway", r["id"])
            self.storage.update_scheduled_job_status(r["id"], "cancelled")
            cancelled.append({"id": r["id"], "content": r["payload"]})
        return {"ambiguous": False, "candidates": [], "cancelled": cancelled}

    def _row_to_view(self, row: dict) -> dict:
        kind = row["kind"]
        fires_at_human = None
        next_fire_human = None
        human_summary = None
        if kind == "reminder" and row["when_utc"] is not None:
            from zoneinfo import ZoneInfo
            dt = datetime.fromtimestamp(row["when_utc"], tz=_tz_mod.utc)
            dt_local = dt.astimezone(ZoneInfo(self._tz))
            fires_at_human = humanize(dt_local)
        elif kind == "cron" and row["cron_spec"]:
            try:
                trigger = CronTrigger.from_crontab(row["cron_spec"], timezone=self._tz)
                nxt = trigger.get_next_fire_time(None, datetime.now(_tz_mod.utc))
                if nxt is not None:
                    next_fire_human = humanize(nxt)
            except Exception:
                pass
            try:
                human_summary = get_description(row["cron_spec"])
            except Exception:
                human_summary = row["cron_spec"]
        return {
            "id": row["id"],
            "kind": kind,
            "fires_at_human": fires_at_human,
            "next_fire_human": next_fire_human,
            "content": row["payload"],
            "status": row["status"],
            "human_summary": human_summary,
            "channel": row["channel"],
            "mode": row["mode"],
        }

    # ---- reconcile ----

    def reconcile(self) -> None:
        """Reconcile APS jobs against the scheduled_jobs table.

        - APS jobs whose mirror DB row is missing → drop the APS job.
        - DB rows with status='pending' whose APS job is missing → re-create
          the APS job from the row.

        The internal '__reconcile__' job (the 60s tick) is always preserved.
        """
        pending_rows = self.storage.list_scheduled_jobs(statuses=("pending",))
        pending_by_id = {str(r["id"]): r for r in pending_rows}

        for job in list(self._scheduler.get_jobs()):
            if job.id == "__reconcile__":
                continue
            if job.id not in pending_by_id:
                try:
                    self._scheduler.remove_job(job.id)
                except Exception:
                    pass

        for row_id, row in pending_by_id.items():
            if self._scheduler.get_job(row_id) is not None:
                continue
            try:
                trigger = self._build_trigger_for_row(row)
            except Exception:
                logger.exception("reconcile: bad trigger for row %s", row_id)
                continue
            kwargs = {"misfire_grace_time": 86400} if row["kind"] == "reminder" else {"coalesce": True}
            self._scheduler.add_job(
                fire_reminder, trigger=trigger,
                kwargs={"job_id": row["id"]}, id=row_id,
                replace_existing=True, **kwargs,
            )

    def _build_trigger_for_row(self, row: dict):
        if row["kind"] == "reminder":
            dt = datetime.fromtimestamp(row["when_utc"], tz=_tz_mod.utc)
            return DateTrigger(run_date=dt)
        elif row["kind"] == "cron":
            return CronTrigger.from_crontab(row["cron_spec"], timezone=self._tz)
        else:
            raise ValueError(f"unknown kind: {row['kind']!r}")
