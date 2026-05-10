from __future__ import annotations
import json
from typing import Optional
from kc_core.messages import (
    UserMessage, AssistantMessage, ToolCallMessage, ToolResultMessage, Message,
)
from kc_supervisor.storage import Storage


class ConversationManager:
    """Persists kc_core message types to SQLite via Storage.

    Maps each Message subclass to a row in the ``messages`` table:

    - UserMessage / AssistantMessage → role + content (no tool_call_json)
    - ToolCallMessage → role="tool_call", tool_call_json holds the JSON
      payload {tool_call_id, tool_name, arguments}
    - ToolResultMessage → role="tool_result", tool_call_json holds
      {tool_call_id, content}
    """

    def __init__(self, storage: Storage) -> None:
        self.s = storage

    def start(self, agent: str, channel: str) -> int:
        return self.s.create_conversation(agent=agent, channel=channel)

    def get_or_create(self, *, channel: str, chat_id: str, agent: str) -> int:
        """Resolve the conversation id for a (channel, chat_id, agent) triple.

        If a mapping exists and the conversation still exists, returns it.
        Otherwise creates a new conversation, sets its title to ``channel:chat_id``,
        and writes the mapping. Mirrors the pattern used by InboundRouter.
        """
        existing = self.s.get_conv_for_chat(channel, chat_id, agent)
        if existing is not None and self.s.get_conversation(existing) is not None:
            return existing
        new_cid = self.s.create_conversation(agent=agent, channel=channel)
        self.s.set_conversation_title(new_cid, f"{channel}:{chat_id}")
        self.s.put_conv_for_chat(channel, chat_id, agent, new_cid)
        return new_cid

    def list_for_agent(self, agent: str) -> list[dict]:
        return self.s.list_conversations(agent=agent)

    def list_all(self, limit: int = 50) -> list[dict]:
        return self.s.list_conversations(limit=limit)

    def append(
        self,
        conversation_id: int,
        msg: Message,
        usage: Optional[dict] = None,
    ) -> int:
        if isinstance(msg, UserMessage):
            return self.s.append_message(conversation_id, "user", msg.content, None)
        if isinstance(msg, AssistantMessage):
            usage_json = json.dumps(usage) if usage is not None else None
            return self.s.append_message(
                conversation_id, "assistant", msg.content, None, usage_json=usage_json,
            )
        if isinstance(msg, ToolCallMessage):
            payload = json.dumps({
                "tool_call_id": msg.tool_call_id,
                "tool_name": msg.tool_name,
                "arguments": msg.arguments,
            })
            return self.s.append_message(conversation_id, "tool_call", None, payload)
        if isinstance(msg, ToolResultMessage):
            payload = json.dumps({
                "tool_call_id": msg.tool_call_id,
                "content": msg.content,
            })
            return self.s.append_message(conversation_id, "tool_result", None, payload)
        raise TypeError(f"unknown message type: {type(msg)}")

    def list_messages(self, conversation_id: int) -> list[Message]:
        out: list[Message] = []
        for row in self.s.list_messages(conversation_id):
            role = row["role"]
            if role == "user":
                out.append(UserMessage(content=row["content"] or ""))
            elif role == "assistant":
                out.append(AssistantMessage(content=row["content"] or ""))
            elif role == "tool_call":
                d = json.loads(row["tool_call_json"])
                out.append(ToolCallMessage(
                    tool_call_id=d["tool_call_id"],
                    tool_name=d["tool_name"],
                    arguments=d["arguments"],
                ))
            elif role == "tool_result":
                d = json.loads(row["tool_call_json"])
                out.append(ToolResultMessage(
                    tool_call_id=d["tool_call_id"],
                    content=d["content"],
                ))
            else:
                raise ValueError(f"unknown role in storage: {role!r}")
        return out

    def list_messages_with_meta(self, conversation_id: int) -> list[tuple[Message, Optional[dict]]]:
        """Like list_messages, but also returns the parsed usage dict for AssistantMessage rows."""
        out: list[tuple[Message, Optional[dict]]] = []
        for row in self.s.list_messages(conversation_id):
            role = row["role"]
            usage: Optional[dict] = None
            if role == "user":
                msg: Message = UserMessage(content=row["content"] or "")
            elif role == "assistant":
                msg = AssistantMessage(content=row["content"] or "")
                # sqlite3.Row supports indexing but not .get() — guard with keys()
                uj = row["usage_json"] if "usage_json" in row.keys() else None
                if uj:
                    try:
                        usage = json.loads(uj)
                    except json.JSONDecodeError:
                        usage = None
            elif role == "tool_call":
                d = json.loads(row["tool_call_json"])
                msg = ToolCallMessage(
                    tool_call_id=d["tool_call_id"],
                    tool_name=d["tool_name"],
                    arguments=d["arguments"],
                )
            elif role == "tool_result":
                d = json.loads(row["tool_call_json"])
                msg = ToolResultMessage(
                    tool_call_id=d["tool_call_id"],
                    content=d["content"],
                )
            else:
                raise ValueError(f"unknown role in storage: {role!r}")
            out.append((msg, usage))
        return out
