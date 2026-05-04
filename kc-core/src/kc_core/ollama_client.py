from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse
import httpx


@dataclass
class ChatResponse:
    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "gemma3:4b",
        timeout: float = 120.0,
        api_key: str | None = None,
    ) -> None:
        stripped = base_url.rstrip("/")
        parsed = urlparse(stripped)
        # If the path is non-empty (beyond root), assume it already includes /v1
        # and we only need to append /chat/completions.
        # If the path is empty or just "/", prepend /v1 as well.
        if parsed.path and parsed.path != "/":
            self._completions_url = f"{stripped}/chat/completions"
        else:
            self._completions_url = f"{stripped}/v1/chat/completions"
        self.base_url = stripped
        self.model = model
        self._timeout = timeout
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ChatResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            r = await http.post(
                self._completions_url,
                json=body,
                headers=self._headers(),
            )
        if r.status_code != 200:
            raise RuntimeError(f"Ollama returned {r.status_code}: {r.text}")
        data = r.json()
        msg = data["choices"][0]["message"]
        text = msg.get("content") or ""
        raw_calls = msg.get("tool_calls") or []
        tool_calls = [
            {
                "id": c["id"],
                "name": c["function"]["name"],
                "arguments": json.loads(c["function"]["arguments"] or "{}"),
            }
            for c in raw_calls
        ]
        return ChatResponse(
            text=text,
            tool_calls=tool_calls,
            finish_reason=data["choices"][0].get("finish_reason", ""),
            raw=data,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ):
        """Async generator yielding text deltas. Tool calls are not surfaced
        via the stream — for tool execution use chat() in the agent loop.
        """
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            body["tools"] = tools

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            async with http.stream(
                "POST",
                self._completions_url,
                json=body,
                headers=self._headers(),
            ) as r:
                if r.status_code != 200:
                    body_bytes = await r.aread()
                    raise RuntimeError(f"Ollama returned {r.status_code}: {body_bytes!r}")
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[len("data: "):]
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    text = delta.get("content")
                    if text:
                        yield text
