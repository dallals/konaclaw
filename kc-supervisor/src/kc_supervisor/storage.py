from __future__ import annotations
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    channel TEXT NOT NULL,
    started_at REAL NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    title TEXT
);
CREATE INDEX IF NOT EXISTS ix_conv_agent ON conversations(agent);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_call_json TEXT,
    usage_json TEXT,
    ts REAL NOT NULL,
    scheduled_job_id INTEGER REFERENCES scheduled_jobs(id) ON DELETE SET NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);
CREATE INDEX IF NOT EXISTS ix_msg_conv ON messages(conversation_id);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    agent TEXT NOT NULL,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    decision TEXT NOT NULL,
    result TEXT,
    undoable INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_audit_agent ON audit(agent);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit(ts);

CREATE TABLE IF NOT EXISTS audit_undo_link (
    audit_id INTEGER PRIMARY KEY,
    undo_op_id INTEGER NOT NULL,
    undone_at REAL,
    FOREIGN KEY(audit_id) REFERENCES audit(id)
);
CREATE INDEX IF NOT EXISTS ix_link_undo ON audit_undo_link(undo_op_id);

CREATE TABLE IF NOT EXISTS mcp_installs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    command TEXT NOT NULL,
    args_json TEXT NOT NULL,
    env_json TEXT NOT NULL,
    why TEXT,
    installed_by_agent TEXT NOT NULL,
    ts REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'running'
);
CREATE INDEX IF NOT EXISTS ix_mcp_status ON mcp_installs(status);

CREATE TABLE IF NOT EXISTS connector_conv_map (
    channel    TEXT NOT NULL,
    chat_id    TEXT NOT NULL,
    agent      TEXT NOT NULL,
    conv_id    INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (channel, chat_id, agent),
    FOREIGN KEY (conv_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    agent TEXT NOT NULL,
    conversation_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    when_utc REAL,
    cron_spec TEXT,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_fired_at REAL,
    created_at REAL NOT NULL,
    mode TEXT NOT NULL DEFAULT 'literal',
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON scheduled_jobs(status);
CREATE INDEX IF NOT EXISTS ix_jobs_conv ON scheduled_jobs(conversation_id);

CREATE TABLE IF NOT EXISTS channel_routing (
    channel          TEXT PRIMARY KEY,
    default_chat_id  TEXT NOT NULL,
    enabled          INTEGER NOT NULL DEFAULT 1
);
"""


class Storage:
    """SQLite storage for kc-supervisor.

    Connections returned by `connect()` use ``isolation_level=None`` (autocommit
    mode), so every statement commits immediately. WAL mode is enabled in
    ``init()`` and persists in the file. Callers needing multi-statement atomicity
    must issue explicit ``BEGIN``/``COMMIT`` themselves.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(conversations)").fetchall()}
            if "pinned" not in cols:
                c.execute("ALTER TABLE conversations ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
            if "title" not in cols:
                c.execute("ALTER TABLE conversations ADD COLUMN title TEXT")
            link_cols = {r["name"] for r in c.execute("PRAGMA table_info(audit_undo_link)").fetchall()}
            if "undone_at" not in link_cols:
                c.execute("ALTER TABLE audit_undo_link ADD COLUMN undone_at REAL")
            msg_cols = {r["name"] for r in c.execute("PRAGMA table_info(messages)").fetchall()}
            if "usage_json" not in msg_cols:
                c.execute("ALTER TABLE messages ADD COLUMN usage_json TEXT")
            if "scheduled_job_id" not in msg_cols:
                # SQLite requires the FK column have a NULL default when added via ALTER.
                # PRAGMA foreign_keys = ON is set per-connection (see connect()), so
                # ON DELETE SET NULL is enforced.
                c.execute(
                    "ALTER TABLE messages ADD COLUMN scheduled_job_id INTEGER "
                    "REFERENCES scheduled_jobs(id) ON DELETE SET NULL"
                )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_scheduled_job_id "
                "ON messages(scheduled_job_id) WHERE scheduled_job_id IS NOT NULL"
            )
            job_cols = {r["name"] for r in c.execute("PRAGMA table_info(scheduled_jobs)").fetchall()}
            if "mode" not in job_cols:
                c.execute("ALTER TABLE scheduled_jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'literal'")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    # ----- conversations -----

    def create_conversation(self, agent: str, channel: str) -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO conversations (agent, channel, started_at) VALUES (?,?,?)",
                (agent, channel, time.time()),
            )
            return int(cur.lastrowid)

    def list_conversations(self, agent: Optional[str] = None, limit: int = 50) -> list[dict]:
        with self.connect() as c:
            if agent is not None:
                rows = c.execute(
                    "SELECT * FROM conversations WHERE agent=? "
                    "ORDER BY pinned DESC, started_at DESC LIMIT ?",
                    (agent, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM conversations "
                    "ORDER BY pinned DESC, started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def set_conversation_pinned(self, conversation_id: int, pinned: bool) -> bool:
        with self.connect() as c:
            cur = c.execute(
                "UPDATE conversations SET pinned=? WHERE id=?",
                (1 if pinned else 0, conversation_id),
            )
            return cur.rowcount > 0

    def set_conversation_title(self, conversation_id: int, title: Optional[str]) -> bool:
        with self.connect() as c:
            cur = c.execute(
                "UPDATE conversations SET title=? WHERE id=?",
                (title, conversation_id),
            )
            return cur.rowcount > 0

    def delete_conversation(self, conversation_id: int) -> bool:
        with self.connect() as c:
            c.execute("BEGIN")
            try:
                c.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
                cur = c.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            return cur.rowcount > 0

    # ----- connector → conversation map -----
    def put_conv_for_chat(
        self, channel: str, chat_id: str, agent: str, conv_id: int,
    ) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT INTO connector_conv_map (channel, chat_id, agent, conv_id) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT(channel, chat_id, agent) DO UPDATE SET "
                "conv_id=excluded.conv_id, updated_at=CURRENT_TIMESTAMP",
                (channel, chat_id, agent, conv_id),
            )

    def get_conv_for_chat(
        self, channel: str, chat_id: str, agent: str,
    ) -> Optional[int]:
        with self.connect() as c:
            row = c.execute(
                "SELECT conv_id FROM connector_conv_map "
                "WHERE channel=? AND chat_id=? AND agent=?",
                (channel, chat_id, agent),
            ).fetchone()
        return row["conv_id"] if row else None

    def clear_conv_for_chat(
        self, channel: str, chat_id: str, agent: str,
    ) -> None:
        with self.connect() as c:
            c.execute(
                "DELETE FROM connector_conv_map "
                "WHERE channel=? AND chat_id=? AND agent=?",
                (channel, chat_id, agent),
            )

    def get_conversation(self, conversation_id: int) -> Optional[dict]:
        """Look up a single conversation by id. Returns None if not found."""
        with self.connect() as c:
            row = c.execute(
                "SELECT * FROM conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
        return dict(row) if row else None

    # ----- messages -----

    def append_message(
        self,
        conversation_id: int,
        role: str,
        content: Optional[str],
        tool_call_json: Optional[str],
        usage_json: Optional[str] = None,
    ) -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO messages (conversation_id, role, content, tool_call_json, usage_json, ts) "
                "VALUES (?,?,?,?,?,?)",
                (conversation_id, role, content, tool_call_json, usage_json, time.time()),
            )
            return int(cur.lastrowid)

    def list_messages(self, conversation_id: int) -> list[dict]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY id ASC",
                (conversation_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_message_scheduled_job_id(
        self, *, message_id: int, scheduled_job_id: int
    ) -> None:
        """Stamp a scheduled_job_id onto an existing message row.

        No-op if the message row doesn't exist; raises sqlite3.IntegrityError
        if the scheduled_job_id is unknown (FK violation).
        """
        with self.connect() as c:
            c.execute(
                "UPDATE messages SET scheduled_job_id=? WHERE id=?",
                (scheduled_job_id, message_id),
            )

    # ----- audit -----

    def append_audit(
        self, *,
        agent: str, tool: str, args_json: str,
        decision: str, result: Optional[str], undoable: bool,
    ) -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO audit (ts, agent, tool, args_json, decision, result, undoable) "
                "VALUES (?,?,?,?,?,?,?)",
                (time.time(), agent, tool, args_json, decision, result, 1 if undoable else 0),
            )
            return int(cur.lastrowid)

    def list_audit(
        self, agent: Optional[str] = None, limit: int = 100,
        decision: Optional[str] = None,
    ) -> list[dict]:
        # LEFT JOIN audit_undo_link so each row carries undone=1 if /undo
        # has already been run on this audit_id (so the dashboard can hide
        # the Undo button instead of letting the user double-fire it).
        clauses: list[str] = []
        params: list[Any] = []
        if agent is not None:
            clauses.append("a.agent=?"); params.append(agent)
        if decision == "denied":
            clauses.append("a.decision=?"); params.append("denied")
        elif decision == "allowed":
            # Allowed rows are written with decision=<source> (tier|callback|override|unknown);
            # the user-facing filter is binary. Anything that is not "denied" counts as allowed.
            clauses.append("a.decision != ?"); params.append("denied")
        sql = (
            "SELECT a.*, "
            "CASE WHEN l.undone_at IS NOT NULL THEN 1 ELSE 0 END AS undone "
            "FROM audit a LEFT JOIN audit_undo_link l ON l.audit_id = a.id "
        )
        if clauses:
            sql += "WHERE " + " AND ".join(clauses) + " "
        sql += "ORDER BY a.ts DESC LIMIT ?"
        params.append(limit)
        with self.connect() as c:
            rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def audit_aggregate_by_tool_prefix(
        self, prefix: str,
    ) -> list[dict]:
        """Per-tool MAX(ts) and COUNT(*) for tools matching prefix%.

        Mirrors `list_audit(decision="allowed")`: allowed audit rows store
        decision=<source> (tier|callback|override|unknown), not the literal
        "allowed", so we filter `decision != 'denied'` to exclude denied rows.
        """
        with self.connect() as c:
            rows = c.execute(
                "SELECT tool, MAX(ts) AS last_ts, COUNT(*) AS n "
                "FROM audit WHERE tool LIKE ? AND decision != 'denied' "
                "GROUP BY tool",
                (prefix + "%",),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_audit_undone(self, audit_id: int) -> bool:
        """Stamp audit_undo_link.undone_at for this audit row. Returns True if a
        link existed (i.e., the audit row had a journaled op to begin with)."""
        with self.connect() as c:
            cur = c.execute(
                "UPDATE audit_undo_link SET undone_at=? WHERE audit_id=?",
                (time.time(), audit_id),
            )
            return cur.rowcount > 0

    # ----- audit ↔ undo cross-reference -----

    def link_audit_undo(self, audit_id: int, undo_op_id: int) -> None:
        """Record that audit row `audit_id` produced kc-sandbox UndoLog row `undo_op_id`.

        First link wins — a second call with a different undo_op_id for the same audit_id
        is silently dropped (one tool call = one journal op in kc-sandbox's contract).
        """
        with self.connect() as c:
            c.execute(
                "INSERT OR IGNORE INTO audit_undo_link (audit_id, undo_op_id) VALUES (?,?)",
                (audit_id, undo_op_id),
            )

    def get_undo_op_for_audit(self, audit_id: int) -> Optional[int]:
        """Look up the kc-sandbox UndoLog eid for an audit row, if any."""
        with self.connect() as c:
            row = c.execute(
                "SELECT undo_op_id FROM audit_undo_link WHERE audit_id=?",
                (audit_id,),
            ).fetchone()
        return row["undo_op_id"] if row else None

    # ----- scheduled jobs -----

    def add_scheduled_job(
        self,
        *,
        kind: str,
        agent: str,
        conversation_id: int,
        channel: str,
        chat_id: str,
        payload: str,
        when_utc: Optional[float],
        cron_spec: Optional[str],
        mode: str = "literal",
    ) -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO scheduled_jobs "
                "(kind, agent, conversation_id, channel, chat_id, payload, "
                " when_utc, cron_spec, status, attempts, created_at, mode) "
                "VALUES (?,?,?,?,?,?,?,?, 'pending', 0, ?, ?)",
                (kind, agent, conversation_id, channel, chat_id, payload,
                 when_utc, cron_spec, time.time(), mode),
            )
            return int(cur.lastrowid)

    def get_scheduled_job(self, job_id: int) -> Optional[dict]:
        with self.connect() as c:
            row = c.execute(
                "SELECT * FROM scheduled_jobs WHERE id=?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_scheduled_jobs(
        self,
        conversation_id: Optional[int] = None,
        statuses: Optional[tuple[str, ...]] = None,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if conversation_id is not None:
            clauses.append("conversation_id=?")
            params.append(conversation_id)
        if statuses is not None:
            placeholders = ",".join("?" * len(statuses))
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM scheduled_jobs {where} ORDER BY id ASC"
        with self.connect() as c:
            rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def update_scheduled_job_status(self, job_id: int, status: str) -> None:
        with self.connect() as c:
            c.execute(
                "UPDATE scheduled_jobs SET status=? WHERE id=?",
                (status, job_id),
            )

    def update_scheduled_job_after_fire(
        self, job_id: int, *, fired_at: float, new_status: str,
    ) -> None:
        with self.connect() as c:
            c.execute(
                "UPDATE scheduled_jobs SET last_fired_at=?, attempts=attempts+1, status=? "
                "WHERE id=?",
                (fired_at, new_status, job_id),
            )

    def delete_scheduled_job(self, job_id: int) -> int:
        with self.connect() as c:
            cur = c.execute("DELETE FROM scheduled_jobs WHERE id=?", (job_id,))
            return cur.rowcount

    # ----- channel routing (cross-channel allowlist) -----

    def get_channel_routing(self, channel: str) -> Optional[dict]:
        with self.connect() as c:
            row = c.execute(
                "SELECT default_chat_id, enabled FROM channel_routing WHERE channel=?",
                (channel,),
            ).fetchone()
        return {"default_chat_id": row["default_chat_id"], "enabled": row["enabled"]} if row else None

    def upsert_channel_routing(self, channel: str, default_chat_id: str, enabled: int) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT INTO channel_routing (channel, default_chat_id, enabled) "
                "VALUES (?,?,?) "
                "ON CONFLICT(channel) DO UPDATE SET "
                "default_chat_id=excluded.default_chat_id, enabled=excluded.enabled",
                (channel, default_chat_id, enabled),
            )

    def list_channel_routing(self) -> list[dict]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT channel, default_chat_id, enabled FROM channel_routing ORDER BY channel ASC"
            ).fetchall()
        return [dict(r) for r in rows]
