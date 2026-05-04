from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator
import pytest
from kc_core.ollama_client import ChatResponse
from kc_core.stream_frames import ChatStreamFrame, TextDelta, ToolCallsBlock, Done


@dataclass
class FakeOllamaClient:
    """Returns a scripted sequence of ChatResponse objects (for non-streaming callers)
    and/or scripted ChatStreamFrame sequences (for streaming callers).

    Each call to .chat() consumes one ChatResponse from `responses`.
    Each call to .chat_stream() consumes one frame list from `stream_responses`.
    If only `responses` is set, .chat_stream() synthesizes frame lists from them.
    """
    responses: list[ChatResponse] = field(default_factory=list)
    stream_responses: list[list[ChatStreamFrame]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    _iter: Iterator[ChatResponse] | None = None
    _stream_iter: Iterator[list[ChatStreamFrame]] | None = None
    model: str = "fake-model"

    def __post_init__(self) -> None:
        self._iter = iter(self.responses)
        # If stream_responses is empty, synthesize from responses
        if not self.stream_responses and self.responses:
            synth: list[list[ChatStreamFrame]] = []
            for r in self.responses:
                frames: list[ChatStreamFrame] = []
                if r.text:
                    frames.append(TextDelta(content=r.text))
                if r.tool_calls:
                    frames.append(ToolCallsBlock(calls=list(r.tool_calls)))
                frames.append(Done(finish_reason=r.finish_reason or "stop"))
                synth.append(frames)
            self.stream_responses = synth
        self._stream_iter = iter(self.stream_responses)

    async def chat(self, messages, tools):
        self.calls.append({"messages": messages, "tools": tools})
        try:
            assert self._iter is not None
            return next(self._iter)
        except StopIteration:
            raise AssertionError("FakeOllamaClient out of scripted responses")

    async def chat_stream(self, messages, tools) -> AsyncIterator[ChatStreamFrame]:
        self.calls.append({"messages": messages, "tools": tools})
        try:
            assert self._stream_iter is not None
            frames = next(self._stream_iter)
        except StopIteration:
            raise AssertionError("FakeOllamaClient out of scripted stream_responses")
        for f in frames:
            yield f


@pytest.fixture
def fake_ollama():
    def _make(*responses: ChatResponse, stream_responses: list[list[ChatStreamFrame]] | None = None) -> FakeOllamaClient:
        kwargs: dict[str, Any] = {"responses": list(responses)}
        if stream_responses is not None:
            kwargs["stream_responses"] = stream_responses
        return FakeOllamaClient(**kwargs)
    return _make
