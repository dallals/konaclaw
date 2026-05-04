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
        for _ in range(self.max_tool_iterations + 1):
            wire = self._build_wire_messages()
            resp = await self.client.chat(messages=wire, tools=self.tools.to_openai_schema())

            # Determine tool calls: prefer native, fall back to JSON-in-text
            calls = list(resp.tool_calls)
            if not calls and resp.text:
                calls = parse_text_tool_calls(resp.text, known_tools=self.tools.names())

            if not calls:
                reply = AssistantMessage(content=resp.text)
                self.history.append(reply)
                return reply

            # Record the assistant's tool-call turn(s) and then each tool result
            for c in calls:
                self.history.append(ToolCallMessage(
                    tool_call_id=c["id"],
                    tool_name=c["name"],
                    arguments=c["arguments"],
                ))
                try:
                    result = self.tools.invoke(c["name"], c["arguments"])
                    content = str(result)
                except KeyError as e:
                    content = f"Error: unknown_tool: {c['name']}"
                except Exception as e:
                    content = f"Error: {type(e).__name__}: {e}"
                self.history.append(ToolResultMessage(
                    tool_call_id=c["id"],
                    content=content,
                ))
            # Loop continues — call the model again with the new tool results
        raise RuntimeError(f"Agent {self.name} exceeded max_tool_iterations={self.max_tool_iterations}")

    def _build_wire_messages(self) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        for m in self.history:
            msgs.append(to_openai_dict(m))
        return msgs
