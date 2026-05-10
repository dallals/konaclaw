from __future__ import annotations
import asyncio
from datetime import datetime, timezone as _tz_mod
from pathlib import Path
from typing import Any, Optional, Protocol
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger

from croniter import croniter
from cron_descriptor import get_description

from kc_supervisor.storage import Storage
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
            jobstores={"default": SQLAlchemyJobStore(url=sqlalchemy_url)},
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

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    # ---- one-shot ----

    def schedule_one_shot(
        self,
        *,
        when: str,
        content: str,
        conversation_id: int,
        channel: str,
        chat_id: str,
        agent: str,
    ) -> dict:
        if not content or not content.strip():
            raise ValueError("content must be 1-4000 chars")
        if len(content) > MAX_PAYLOAD_CHARS:
            raise ValueError(f"content must be 1-{MAX_PAYLOAD_CHARS} chars")
        target = parse_when(when, self._tz)
        if is_past(target):
            raise ValueError(f"'when' resolves to the past: {when!r}")
        target_utc = target.astimezone(_tz_mod.utc)

        job_id = self.storage.add_scheduled_job(
            kind="reminder", agent=agent, conversation_id=conversation_id,
            channel=channel, chat_id=chat_id, payload=content,
            when_utc=target_utc.timestamp(), cron_spec=None,
        )
        try:
            self._scheduler.add_job(
                self.runner.fire, trigger=DateTrigger(run_date=target),
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
    ) -> dict:
        if not content or not content.strip():
            raise ValueError("content must be 1-4000 chars")
        if len(content) > MAX_PAYLOAD_CHARS:
            raise ValueError(f"content must be 1-{MAX_PAYLOAD_CHARS} chars")
        if not croniter.is_valid(cron):
            raise ValueError(f"invalid cron: {cron!r}")

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
            channel=channel, chat_id=chat_id, payload=content,
            when_utc=None, cron_spec=cron,
        )
        try:
            self._scheduler.add_job(
                self.runner.fire, trigger=trigger,
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
