from __future__ import annotations
import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol
from kc_core.messages import (
    UserMessage, AssistantMessage, ToolCallMessage, ToolResultMessage,
    Message, to_openai_dict,
)
from kc_core.tools import ToolRegistry
from kc_core.tool_call_parser import parse_text_tool_calls


PermissionCheck = Callable[[str, str, dict[str, Any]], tuple[bool, Optional[str]]]
# (agent_name, tool_name, arguments) -> (allowed, optional_deny_reason)


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
    permission_check: Optional[PermissionCheck] = None

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

            # Record ALL tool calls from this turn first (so they're consecutive
            # in history), then append results. This matches the OpenAI wire
            # format: one assistant message with tool_calls=[a,b,...] followed
            # by N separate tool result messages.
            results: list[tuple[str, str]] = []
            for c in calls:
                self.history.append(ToolCallMessage(
                    tool_call_id=c["id"],
                    tool_name=c["name"],
                    arguments=c["arguments"],
                ))
                # NEW: permission check — short-circuits before tool execution.
                # On deny, push the deny message into `results` so it lands in
                # the second loop alongside any allowed results.
                if self.permission_check is not None:
                    result = self.permission_check(self.name, c["name"], c["arguments"])
                    if asyncio.iscoroutine(result):
                        result = await result
                    allowed, reason = result
                    if not allowed:
                        results.append((c["id"], f"Denied: {reason or 'permission_check returned False'}"))
                        continue
                try:
                    result = self.tools.invoke(c["name"], c["arguments"])
                    content = str(result)
                except KeyError:
                    content = f"Error: unknown_tool: {c['name']}"
                except Exception as e:
                    content = f"Error: {type(e).__name__}: {e}"
                results.append((c["id"], content))
            for call_id, content in results:
                self.history.append(ToolResultMessage(
                    tool_call_id=call_id,
                    content=content,
                ))
            # Loop continues — call the model again with the new tool results
        raise RuntimeError(f"Agent {self.name} exceeded max_tool_iterations={self.max_tool_iterations}")

    def _build_wire_messages(self) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        i = 0
        while i < len(self.history):
            m = self.history[i]
            if isinstance(m, ToolCallMessage):
                # Collect all consecutive ToolCallMessages — they were emitted in
                # the same model turn and must serialize as ONE assistant message.
                batch: list[ToolCallMessage] = []
                while i < len(self.history) and isinstance(self.history[i], ToolCallMessage):
                    batch.append(self.history[i])
                    i += 1
                msgs.append({
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": tc.tool_call_id,
                            "type": "function",
                            "function": {
                                "name": tc.tool_name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in batch
                    ],
                })
            else:
                msgs.append(to_openai_dict(m))
                i += 1
        return msgs
