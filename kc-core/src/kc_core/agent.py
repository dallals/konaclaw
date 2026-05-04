from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol
from kc_core.messages import (
    UserMessage, AssistantMessage, ToolCallMessage, ToolResultMessage,
    Message, to_openai_dict,
)
from kc_core.tools import ToolRegistry
from kc_core.tool_call_parser import parse_text_tool_calls


class _ChatClient(Protocol):
    model: str
    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]): ...


@dataclass
class Agent:
    name: str
    client: _ChatClient
    system_prompt: str
    tools: ToolRegistry
    max_tool_iterations: int = 10
    history: list[Message] = field(default_factory=list)

    async def send(self, user_text: str) -> AssistantMessage:
        self.history.append(UserMessage(content=user_text))
        return await self._run_loop()

    async def _run_loop(self) -> AssistantMessage:
        # Tools come in Task 6. For now, always one round-trip.
        wire = self._build_wire_messages()
        resp = await self.client.chat(messages=wire, tools=self.tools.to_openai_schema())
        reply = AssistantMessage(content=resp.text)
        self.history.append(reply)
        return reply

    def _build_wire_messages(self) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        for m in self.history:
            msgs.append(to_openai_dict(m))
        return msgs
