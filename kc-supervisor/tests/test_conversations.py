import pytest
from kc_supervisor.storage import Storage
from kc_supervisor.conversations import ConversationManager
from kc_core.messages import UserMessage, AssistantMessage, ToolCallMessage, ToolResultMessage


def test_start_appends_persists(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    cm = ConversationManager(storage=s)
    cid = cm.start(agent="kc", channel="dashboard")
    cm.append(cid, UserMessage("hi"))
    cm.append(cid, AssistantMessage("hello"))
    msgs = cm.list_messages(cid)
    assert len(msgs) == 2
    assert isinstance(msgs[0], UserMessage)
    assert msgs[0].content == "hi"
    assert msgs[1].content == "hello"


def test_tool_call_round_trip(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    cm = ConversationManager(storage=s)
    cid = cm.start(agent="kc", channel="dashboard")
    cm.append(cid, ToolCallMessage(tool_call_id="c1", tool_name="echo", arguments={"text": "hi"}))
    cm.append(cid, ToolResultMessage(tool_call_id="c1", content="hi"))
    msgs = cm.list_messages(cid)
    assert isinstance(msgs[0], ToolCallMessage)
    assert msgs[0].tool_name == "echo"
    assert msgs[0].arguments == {"text": "hi"}
    assert isinstance(msgs[1], ToolResultMessage)
    assert msgs[1].tool_call_id == "c1"
    assert msgs[1].content == "hi"


def test_list_for_agent(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    cm = ConversationManager(storage=s)
    cm.start(agent="kc", channel="dashboard")
    cm.start(agent="EmailBot", channel="dashboard")
    convs = cm.list_for_agent("kc")
    assert len(convs) == 1
    assert convs[0]["agent"] == "kc"


def test_list_all(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    cm = ConversationManager(storage=s)
    cm.start(agent="kc", channel="dashboard")
    cm.start(agent="EmailBot", channel="dashboard")
    convs = cm.list_all()
    assert len(convs) == 2


def test_append_unknown_message_type_raises(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    cm = ConversationManager(storage=s)
    cid = cm.start(agent="kc", channel="dashboard")
    with pytest.raises(TypeError):
        cm.append(cid, "not a Message")  # type: ignore[arg-type]


def test_message_order_preserved(tmp_path):
    s = Storage(tmp_path / "kc.db"); s.init()
    cm = ConversationManager(storage=s)
    cid = cm.start(agent="kc", channel="dashboard")
    cm.append(cid, UserMessage("first"))
    cm.append(cid, AssistantMessage("second"))
    cm.append(cid, UserMessage("third"))
    msgs = cm.list_messages(cid)
    assert [m.content for m in msgs] == ["first", "second", "third"]


def test_append_assistant_persists_usage(tmp_path):
    import json
    from kc_supervisor.storage import Storage
    from kc_supervisor.conversations import ConversationManager
    from kc_core.messages import AssistantMessage
    s = Storage(tmp_path / "kc.db"); s.init()
    cm = ConversationManager(s)
    cid = cm.start("kona", "dashboard")
    cm.append(cid, AssistantMessage(content="hi"), usage={"output_tokens": 4, "ttfb_ms": 80.0})
    rows = s.list_messages(cid)
    assert rows[0]["role"] == "assistant"
    assert json.loads(rows[0]["usage_json"]) == {"output_tokens": 4, "ttfb_ms": 80.0}


def test_list_messages_with_meta_returns_usage(tmp_path):
    from kc_supervisor.storage import Storage
    from kc_supervisor.conversations import ConversationManager
    from kc_core.messages import UserMessage, AssistantMessage
    s = Storage(tmp_path / "kc.db"); s.init()
    cm = ConversationManager(s)
    cid = cm.start("kona", "dashboard")
    cm.append(cid, UserMessage(content="hi"))
    cm.append(cid, AssistantMessage(content="hello"), usage={"output_tokens": 1})
    pairs = cm.list_messages_with_meta(cid)
    assert len(pairs) == 2
    msg0, meta0 = pairs[0]
    assert isinstance(msg0, UserMessage)
    assert meta0 is None
    msg1, meta1 = pairs[1]
    assert isinstance(msg1, AssistantMessage)
    assert meta1 == {"output_tokens": 1}
