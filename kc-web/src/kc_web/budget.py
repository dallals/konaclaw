from __future__ import annotations
import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS web_calls (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_utc      TEXT    NOT NULL,
  day_utc     TEXT    NOT NULL,
  session_id  TEXT    NOT NULL,
  tool_name   TEXT    NOT NULL,
  blocked     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_web_calls_day     ON web_calls (day_utc);
CREATE INDEX IF NOT EXISTS idx_web_calls_session ON web_calls (session_id);
"""


def _today_utc() -> str:
    """Patchable indirection for tests."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class BudgetStore:
    """SQLite-backed call counter for web_search + web_fetch.

    A module-level asyncio.Lock serializes the read-then-write so two concurrent
    tool calls cannot both squeak past at cap-1. SQLite writes inside the lock
    are sub-millisecond; web calls themselves take seconds, so contention is
    irrelevant in practice.
    """

    def __init__(
        self,
        *,
        db_path: Path,
        session_id: str,
        session_soft_cap: int,
        daily_hard_cap: int,
    ) -> None:
        self.db_path = db_path
        self.session_id = session_id
        self.session_soft_cap = session_soft_cap
        self.daily_hard_cap = daily_hard_cap
        self._lock = asyncio.Lock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path, isolation_level=None)  # autocommit

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _count_for_day(self, conn: sqlite3.Connection, day: str) -> int:
        row = conn.execute(
            "SELECT COUNT(*) FROM web_calls WHERE day_utc=? AND blocked=0",
            (day,),
        ).fetchone()
        return int(row[0])

    def _count_for_session(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT COUNT(*) FROM web_calls WHERE session_id=? AND blocked=0",
            (self.session_id,),
        ).fetchone()
        return int(row[0])

    def _record(
        self,
        conn: sqlite3.Connection,
        tool_name: str,
        day: str,
        blocked: bool,
    ) -> None:
        conn.execute(
            "INSERT INTO web_calls (ts_utc, day_utc, session_id, tool_name, blocked) "
            "VALUES (?, ?, ?, ?, ?)",
            (_now_utc(), day, self.session_id, tool_name, 1 if blocked else 0),
        )

    async def check_and_record(
        self, tool_name: str
    ) -> tuple[bool, dict[str, Any] | None]:
        """Atomic: check both caps; on success increment; on failure record blocked
        and return error dict. Daily cap is checked first (harder limit)."""
        async with self._lock:
            day = _today_utc()
            with self._conn() as conn:
                if self._count_for_day(conn, day) >= self.daily_hard_cap:
                    self._record(conn, tool_name, day, blocked=True)
                    return False, {
                        "error": "daily_cap_exceeded",
                        "limit": self.daily_hard_cap,
                    }
                if self._count_for_session(conn) >= self.session_soft_cap:
                    self._record(conn, tool_name, day, blocked=True)
                    return False, {
                        "error": "session_cap_exceeded",
                        "limit": self.session_soft_cap,
                    }
                self._record(conn, tool_name, day, blocked=False)
        return True, None

    def summary(self) -> dict[str, Any]:
        day = _today_utc()
        with self._conn() as conn:
            return {
                "session_id": self.session_id,
                "session_count": self._count_for_session(conn),
                "session_cap": self.session_soft_cap,
                "daily_count": self._count_for_day(conn, day),
                "daily_cap": self.daily_hard_cap,
                "day_utc": day,
            }
