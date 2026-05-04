import pytest
from kc_core.tools import Tool, ToolRegistry


def echo_impl(text: str) -> str:
    return text


def test_register_and_get_tool():
    t = Tool(
        name="echo",
        description="Echo input",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        impl=echo_impl,
    )
    r = ToolRegistry()
    r.register(t)
    assert r.get("echo") is t


def test_get_unknown_tool_raises():
    r = ToolRegistry()
    with pytest.raises(KeyError, match="unknown_tool"):
        r.get("unknown_tool")


def test_invoke_calls_impl_with_kwargs():
    t = Tool(name="echo", description="", parameters={}, impl=echo_impl)
    r = ToolRegistry()
    r.register(t)
    assert r.invoke("echo", {"text": "hi"}) == "hi"


def test_to_openai_schema_returns_function_list():
    t = Tool(
        name="echo",
        description="Echo input",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        impl=echo_impl,
    )
    r = ToolRegistry()
    r.register(t)
    schema = r.to_openai_schema()
    assert schema == [{
        "type": "function",
        "function": {
            "name": "echo",
            "description": "Echo input",
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
        },
    }]


def test_register_duplicate_raises():
    t = Tool(name="echo", description="", parameters={}, impl=echo_impl)
    r = ToolRegistry()
    r.register(t)
    with pytest.raises(ValueError, match="already registered"):
        r.register(t)
