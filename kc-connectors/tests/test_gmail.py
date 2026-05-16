from __future__ import annotations

from unittest.mock import MagicMock

from kc_connectors.gmail_adapter import build_gmail_tools


def fake_service():
    svc = MagicMock()
    svc.users().threads().list().execute.return_value = {"threads": [{"id": "t1"}, {"id": "t2"}]}
    # `.get(...).execute()` is reused by both `search` (metadata enrichment) and
    # `read_thread`. The fake message carries headers AND a snippet so both
    # consumers see what they expect.
    svc.users().threads().get().execute.return_value = {
        "messages": [
            {
                "snippet": "hello",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Invoice March"},
                        {"name": "From", "value": "billing@acme.com"},
                        {"name": "Date", "value": "Mon, 04 Mar 2024 12:00:00 +0000"},
                    ],
                },
            },
        ],
    }
    svc.users().drafts().create().execute.return_value = {"id": "d1"}
    svc.users().drafts().send().execute.return_value = {"id": "m1"}
    return svc


def test_gmail_search_includes_subject_sender_and_date():
    svc = fake_service()
    tools = build_gmail_tools(service=svc)
    out = tools["gmail.search"].impl(query="from:billing")
    # Thread IDs still surfaced so the model can call read_thread next.
    assert "t1" in out and "t2" in out
    # Enriched fields appear so the user can scan threads without an extra call.
    assert "Invoice March" in out
    assert "billing@acme.com" in out
    assert "2024" in out
    assert "hello" in out  # snippet


def test_gmail_search_empty_threads():
    svc = MagicMock()
    svc.users().threads().list().execute.return_value = {"threads": []}
    tools = build_gmail_tools(service=svc)
    out = tools["gmail.search"].impl(query="from:nobody")
    assert out == "(no threads)"


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
