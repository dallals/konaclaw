from kc_core.tool_call_parser import parse_text_tool_calls


def test_returns_empty_for_plain_text():
    assert parse_text_tool_calls("Hello, world.", known_tools=["echo"]) == []


def test_parses_fenced_json_tool_call():
    text = '''Sure, I'll do that.
```json
{"tool": "echo", "arguments": {"text": "hi"}}
```
'''
    calls = parse_text_tool_calls(text, known_tools=["echo"])
    assert len(calls) == 1
    assert calls[0]["name"] == "echo"
    assert calls[0]["arguments"] == {"text": "hi"}
    assert calls[0]["id"].startswith("call_")


def test_ignores_unknown_tool():
    text = '```json\n{"tool": "frobnicate", "arguments": {}}\n```'
    assert parse_text_tool_calls(text, known_tools=["echo"]) == []


def test_parses_inline_json_when_obvious():
    text = '{"tool": "echo", "arguments": {"text": "hi"}}'
    calls = parse_text_tool_calls(text, known_tools=["echo"])
    assert len(calls) == 1
    assert calls[0]["name"] == "echo"


def test_handles_malformed_json_gracefully():
    text = '```json\n{"tool": "echo", "arguments": {bad}}\n```'
    assert parse_text_tool_calls(text, known_tools=["echo"]) == []
