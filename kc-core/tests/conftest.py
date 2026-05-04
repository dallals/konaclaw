from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Iterator
import pytest
from kc_core.ollama_client import ChatResponse


@dataclass
class FakeOllamaClient:
    """Returns a scripted sequence of ChatResponse objects."""
    responses: list[ChatResponse] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    _iter: Iterator[ChatResponse] | None = None
    model: str = "fake-model"

    def __post_init__(self) -> None:
        self._iter = iter(self.responses)

    async def chat(self, messages, tools):
        self.calls.append({"messages": messages, "tools": tools})
        try:
            assert self._iter is not None
            return next(self._iter)
        except StopIteration:
            raise AssertionError("FakeOllamaClient out of scripted responses")


@pytest.fixture
def fake_ollama():
    def _make(*responses: ChatResponse) -> FakeOllamaClient:
        return FakeOllamaClient(responses=list(responses))
    return _make
