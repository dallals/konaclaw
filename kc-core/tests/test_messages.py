from kc_core.messages import (
    UserMessage, AssistantMessage, ToolCallMessage,
    ToolResultMessage, to_openai_dict,
)


def test_user_message_serializes():
    m = UserMessage(content="hello")
    assert to_openai_dict(m) == {"role": "user", "content": "hello"}


def test_assistant_text_message_serializes():
    m = AssistantMessage(content="hi there")
    assert to_openai_dict(m) == {"role": "assistant", "content": "hi there"}


def test_tool_call_message_serializes():
    m = ToolCallMessage(
        tool_call_id="call_1",
        tool_name="echo",
        arguments={"text": "hi"},
    )
    d = to_openai_dict(m)
    assert d["role"] == "assistant"
    assert d["tool_calls"][0]["id"] == "call_1"
    assert d["tool_calls"][0]["function"]["name"] == "echo"
    assert d["tool_calls"][0]["function"]["arguments"] == '{"text": "hi"}'


def test_tool_result_message_serializes():
    m = ToolResultMessage(tool_call_id="call_1", content="hi")
    assert to_openai_dict(m) == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "hi",
    }
