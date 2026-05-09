from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Optional

from kc_connectors.base import Connector, InboundCallback, MessageEnvelope


def _escape_applescript(s: str) -> str:
    """Escape a string for safe interpolation into an AppleScript double-quoted literal.

    Order matters: backslash must be escaped before double-quote, otherwise the
    backslashes added by the quote-escape pass would themselves get doubled.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')


class IMessageConnector(Connector):
    capabilities = {"send"}

    def __init__(
        self,
        chat_db_path: Path,
        allowlist: set[str],
        poll_interval_s: float = 1.0,
    ) -> None:
        super().__init__(name="imessage")
        self.chat_db_path = Path(chat_db_path)
        self.allowlist = set(allowlist)
        self._poll_interval = poll_interval_s
        self._last_rowid = 0
        self._task: Optional[asyncio.Task] = None
        self._on_envelope: Optional[InboundCallback] = None

    async def start(self, supervisor) -> None:
        self._on_envelope = supervisor.handle_inbound
        con = sqlite3.connect(f"file:{self.chat_db_path}?mode=ro", uri=True)
        try:
            cur = con.execute("SELECT IFNULL(MAX(ROWID),0) FROM message")
            self._last_rowid = cur.fetchone()[0]
        finally:
            con.close()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await self._poll_once()
            except Exception:
                pass
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        con = sqlite3.connect(f"file:{self.chat_db_path}?mode=ro", uri=True)
        try:
            con.row_factory = sqlite3.Row
            rows = con.execute("""
                SELECT m.ROWID, m.text, m.is_from_me, h.id as handle_id, c.guid as chat_guid
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
                JOIN chat_message_join j ON j.message_id = m.ROWID
                JOIN chat c ON c.ROWID = j.chat_id
                WHERE m.ROWID > ?
                ORDER BY m.ROWID ASC
            """, (self._last_rowid,)).fetchall()
        finally:
            con.close()

        for r in rows:
            self._last_rowid = max(self._last_rowid, r["ROWID"])
            if r["is_from_me"]:
                continue
            if r["handle_id"] not in self.allowlist:
                continue
            env = MessageEnvelope(
                channel=self.name,
                chat_id=r["chat_guid"],
                sender_id=r["handle_id"],
                content=r["text"] or "",
                attachments=[],
            )
            if self._on_envelope is not None:
                await self._on_envelope(env)

    async def send(self, chat_id: str, content: str, attachments=None) -> None:
        # chat_id is the chat guid; for DM we recover the handle from the guid.
        handle = chat_id.split(";")[-1]
        if handle not in self.allowlist:
            raise PermissionError(f"chat {chat_id} not allowlisted")
        safe_content = _escape_applescript(content)
        safe_handle = _escape_applescript(handle)
        script = (
            'tell application "Messages"\n'
            '    set targetService to 1st service whose service type = iMessage\n'
            f'    set targetBuddy to buddy "{safe_handle}" of targetService\n'
            f'    send "{safe_content}" to targetBuddy\n'
            'end tell\n'
        )
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
