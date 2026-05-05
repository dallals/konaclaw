from kc_core.messages import AssistantMessage
from kc_core.stream_frames import (
    TextDelta, ToolCallsBlock, Done,
    TokenDelta, ToolCallStart, ToolResult, Complete,
)


def test_text_delta_holds_content():
    f = TextDelta(content="hello")
    assert f.content == "hello"


def test_tool_calls_block_holds_calls():
    calls = [{"id": "c1", "name": "echo", "arguments": {"text": "hi"}}]
    f = ToolCallsBlock(calls=calls)
    assert f.calls == calls


def test_done_finish_reason():
    f = Done(finish_reason="stop")
    assert f.finish_reason == "stop"


def test_token_delta_holds_content():
    f = TokenDelta(content="hi")
    assert f.content == "hi"


def test_tool_call_start_holds_call():
    call = {"id": "c1", "name": "echo", "arguments": {"text": "hi"}}
    f = ToolCallStart(call=call)
    assert f.call == call


def test_tool_result_holds_call_id_and_content():
    f = ToolResult(call_id="c1", content="hi")
    assert f.call_id == "c1"
    assert f.content == "hi"


def test_complete_holds_assistant_message():
    msg = AssistantMessage(content="done")
    f = Complete(reply=msg)
    assert f.reply is msg
