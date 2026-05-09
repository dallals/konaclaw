from __future__ import annotations

from unittest.mock import MagicMock

from kc_connectors.gmail_adapter import build_gmail_tools


def fake_service():
    svc = MagicMock()
    svc.users().threads().list().execute.return_value = {"threads": [{"id": "t1"}, {"id": "t2"}]}
    svc.users().threads().get().execute.return_value = {"messages": [{"snippet": "hello"}]}
    svc.users().drafts().create().execute.return_value = {"id": "d1"}
    svc.users().drafts().send().execute.return_value = {"id": "m1"}
    return svc


def test_gmail_search():
    svc = fake_service()
    tools = build_gmail_tools(service=svc)
    out = tools["gmail.search"].impl(query="from:billing")
    assert "t1" in out and "t2" in out


def test_gmail_read_thread():
    svc = fake_service()
    tools = build_gmail_tools(service=svc)
    out = tools["gmail.read_thread"].impl(thread_id="t1")
    assert "hello" in out


def test_gmail_draft_then_send():
    svc = fake_service()
    tools = build_gmail_tools(service=svc)
    d = tools["gmail.draft"].impl(to="x@y", subject="s", body="b")
    assert "d1" in d
    s = tools["gmail.send"].impl(draft_id="d1")
    assert "m1" in s
