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


from kc_core.stream_frames import ChatUsage, TurnUsage


def test_chat_usage_frame_default_fields():
    u = ChatUsage(
        input_tokens=120,
        output_tokens=42,
        ttfb_ms=314.5,
        generation_ms=1280.0,
        usage_reported=True,
    )
    assert u.input_tokens == 120
    assert u.output_tokens == 42
    assert u.ttfb_ms == 314.5
    assert u.generation_ms == 1280.0
    assert u.usage_reported is True


def test_turn_usage_frame_carries_call_index():
    u = TurnUsage(
        call_index=1,
        input_tokens=300,
        output_tokens=12,
        ttfb_ms=80.0,
        generation_ms=110.0,
        usage_reported=False,
    )
    assert u.call_index == 1
    assert u.usage_reported is False


def test_chat_usage_is_chat_stream_frame():
    from kc_core.stream_frames import ChatStreamFrame
    f: ChatStreamFrame = ChatUsage(0, 0, 0.0, 0.0, False)
    assert f is not None


def test_turn_usage_is_stream_frame():
    from kc_core.stream_frames import StreamFrame
    f: StreamFrame = TurnUsage(0, 0, 0, 0.0, 0.0, False)
    assert f is not None
