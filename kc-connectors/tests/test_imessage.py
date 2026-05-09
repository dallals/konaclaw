from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kc_connectors.imessage_adapter import IMessageConnector, _escape_applescript


def make_chat_db(p: Path):
    con = sqlite3.connect(p)
    con.executescript("""
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT);
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, handle_id INTEGER, is_from_me INTEGER, date INTEGER);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
    """)
    con.executescript("""
        INSERT INTO chat (ROWID, guid) VALUES (1, 'iMessage;-;+15555550100');
        INSERT INTO handle (ROWID, id) VALUES (1, '+15555550100');
    """)
    con.commit()
    return con


def insert_msg(con, rowid, text, handle, chat=1, from_me=0):
    con.execute("INSERT INTO message (ROWID, text, handle_id, is_from_me, date) VALUES (?,?,?,?,?)",
                (rowid, text, handle, from_me, 0))
    con.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (?,?)", (chat, rowid))
    con.commit()


@pytest.mark.asyncio
async def test_poll_reads_new_messages_only(tmp_path):
    db = tmp_path / "chat.db"
    con = make_chat_db(db)
    insert_msg(con, 1, "hi from outside", handle=1)

    received = []
    async def cb(env): received.append(env)
    c = IMessageConnector(chat_db_path=db, allowlist={"+15555550100"})
    c._on_envelope = cb
    await c._poll_once()
    assert len(received) == 1
    assert received[0].content == "hi from outside"

    await c._poll_once()
    assert len(received) == 1  # no duplicates

    insert_msg(con, 2, "another", handle=1)
    await c._poll_once()
    assert len(received) == 2
    con.close()


@pytest.mark.asyncio
async def test_non_allowlisted_dropped(tmp_path):
    db = tmp_path / "chat.db"
    con = make_chat_db(db)
    insert_msg(con, 1, "from blocked", handle=1, chat=1)
    received = []
    async def cb(env): received.append(env)
    c = IMessageConnector(chat_db_path=db, allowlist={"+15555550999"})
    c._on_envelope = cb
    await c._poll_once()
    assert received == []
    con.close()


@pytest.mark.asyncio
async def test_messages_from_me_skipped(tmp_path):
    db = tmp_path / "chat.db"
    con = make_chat_db(db)
    insert_msg(con, 1, "I sent this", handle=1, from_me=1)
    received = []
    async def cb(env): received.append(env)
    c = IMessageConnector(chat_db_path=db, allowlist={"+15555550100"})
    c._on_envelope = cb
    await c._poll_once()
    assert received == []
    con.close()


def test_applescript_escape_handles_backslash_and_quote():
    assert _escape_applescript('back\\slash and "quotes"') == 'back\\\\slash and \\"quotes\\"'
    assert _escape_applescript("plain") == "plain"
