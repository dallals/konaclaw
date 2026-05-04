from __future__ import annotations
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from kc_sandbox.journal import Journal


@dataclass
class UndoEntry:
    agent: str
    tool: str
    reverse_kind: str
    reverse_payload: dict[str, Any]
    id: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    applied_at: Optional[float] = None


class UndoLog:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def init(self) -> None:
        """Create the undo_log table if it doesn't exist.

        Schema note (deviation from umbrella spec): the spec calls for an
        `audit_id` FK into a future `audit` table. kc-sandbox v1 has no
        audit table yet, so this layer keeps the entries self-contained
        with `agent`, `tool`, and `created_at` columns instead. When
        kc-supervisor introduces the audit table, decide whether to add
        an `audit_id` column here or link via a separate join table.
        """
        with sqlite3.connect(self.db_path) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS undo_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    reverse_kind TEXT NOT NULL,
                    reverse_payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    applied_at REAL
                )
            """)

    def record(self, e: UndoEntry) -> int:
        try:
            payload_json = json.dumps(e.reverse_payload)
        except TypeError as exc:
            raise ValueError(
                f"reverse_payload for entry ({e.agent}/{e.tool}) is not JSON-serializable: {exc}"
            ) from exc
        with sqlite3.connect(self.db_path) as c:
            cur = c.execute(
                "INSERT INTO undo_log (agent, tool, reverse_kind, reverse_payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (e.agent, e.tool, e.reverse_kind, payload_json, e.created_at),
            )
            return int(cur.lastrowid)

    def get(self, eid: int) -> UndoEntry:
        with sqlite3.connect(self.db_path) as c:
            row = c.execute(
                "SELECT id, agent, tool, reverse_kind, reverse_payload, created_at, applied_at "
                "FROM undo_log WHERE id = ?", (eid,)
            ).fetchone()
        if row is None:
            raise KeyError(f"undo entry {eid} not found")
        return UndoEntry(
            id=row[0], agent=row[1], tool=row[2], reverse_kind=row[3],
            reverse_payload=json.loads(row[4]),
            created_at=row[5], applied_at=row[6],
        )

    def mark_applied(self, eid: int) -> None:
        with sqlite3.connect(self.db_path) as c:
            cur = c.execute(
                "UPDATE undo_log SET applied_at = ? WHERE id = ?", (time.time(), eid)
            )
            if cur.rowcount == 0:
                raise KeyError(f"undo entry {eid} not found")


class Undoer:
    def __init__(self, journals: dict[str, Journal], log: UndoLog) -> None:
        self.journals = journals
        self.log = log

    def undo(self, entry_id: int) -> None:
        e = self.log.get(entry_id)
        if e.applied_at is not None:
            raise ValueError(f"undo {entry_id} already applied at {e.applied_at}")

        if e.reverse_kind == "git-revert":
            share = e.reverse_payload["share"]
            sha = e.reverse_payload["sha"]
            j = self.journals.get(share)
            if j is None:
                raise KeyError(f"no journal for share {share!r}")
            j.revert(sha)
            self.log.mark_applied(entry_id)
            return

        raise NotImplementedError(f"unknown reverse_kind: {e.reverse_kind!r}")
