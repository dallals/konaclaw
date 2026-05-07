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
    ts REAL NOT NULL,
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
    ) -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO messages (conversation_id, role, content, tool_call_json, ts) "
                "VALUES (?,?,?,?,?)",
                (conversation_id, role, content, tool_call_json, time.time()),
            )
            return int(cur.lastrowid)

    def list_messages(self, conversation_id: int) -> list[dict]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY id ASC",
                (conversation_id,),
            ).fetchall()
        return [dict(r) for r in rows]

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
        if decision is not None:
            clauses.append("a.decision=?"); params.append(decision)
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
