from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any

from kc_core.tools import Tool


GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]


def build_gmail_tools(service: Any) -> dict[str, Tool]:
    """`service` is a googleapiclient discovery object for gmail v1."""

    def search(query: str, max_results: int = 10) -> str:
        r = service.users().threads().list(userId="me", q=query, maxResults=max_results).execute()
        threads = r.get("threads", [])
        return "\n".join(f"thread:{t['id']}" for t in threads) or "(no threads)"

    def read_thread(thread_id: str) -> str:
        r = service.users().threads().get(userId="me", id=thread_id).execute()
        msgs = r.get("messages", [])
        return "\n\n".join(m.get("snippet", "") for m in msgs)

    def draft(to: str, subject: str, body: str) -> str:
        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        r = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
        return f"draft created: {r['id']}"

    def send(draft_id: str) -> str:
        r = service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
        return f"sent: {r['id']}"

    def make(name, desc, params, impl):
        return Tool(name=name, description=desc, parameters=params, impl=impl)

    return {
        "gmail.search": make(
            "gmail.search",
            "Search Gmail threads.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
            search,
        ),
        "gmail.read_thread": make(
            "gmail.read_thread",
            "Read a Gmail thread.",
            {
                "type": "object",
                "properties": {"thread_id": {"type": "string"}},
                "required": ["thread_id"],
            },
            read_thread,
        ),
        "gmail.draft": make(
            "gmail.draft",
            "Save a Gmail draft.",
            {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
            draft,
        ),
        "gmail.send": make(
            "gmail.send",
            "Send a previously-saved draft. Destructive.",
            {
                "type": "object",
                "properties": {"draft_id": {"type": "string"}},
                "required": ["draft_id"],
            },
            send,
        ),
    }


def build_gmail_service(credentials):
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=credentials)
