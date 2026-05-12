from pathlib import Path
import textwrap
import pytest
from kc_subagents.templates import SubagentTemplate, load_template_file, TemplateLoadError

def write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / f"{name}.yaml"
    p.write_text(textwrap.dedent(body))
    return p

def test_load_minimal_template(tmp_path: Path):
    p = write(tmp_path, "web-researcher", """
        name: web-researcher
        model: claude-opus-4-7
        system_prompt: |
          You research things.
    """)
    t = load_template_file(p)
    assert isinstance(t, SubagentTemplate)
    assert t.name == "web-researcher"
    assert t.model == "claude-opus-4-7"
    assert t.system_prompt.strip() == "You research things."
    assert t.tools == {}
    assert t.mcp_servers == []
    assert t.timeout_seconds == 300
    assert t.max_tool_calls == 50

def test_load_full_template(tmp_path: Path):
    p = write(tmp_path, "coder", """
        name: coder
        description: A coding subagent.
        version: "1.0"
        model: claude-opus-4-7
        model_options:
          temperature: 0.2
        system_prompt: |
          You code.
        tools:
          terminal_run: {}
          skill_view: {}
        mcp_servers: [zapier]
        mcp_action_filter:
          zapier: [gmail_find_email]
        memory:
          mode: read-only
          scope: research/
        shares: [downloads-readable]
        permission_overrides:
          terminal_run: MUTATING
        timeout_seconds: 600
        max_tool_calls: 100
    """)
    t = load_template_file(p)
    assert t.tools == {"terminal_run": {}, "skill_view": {}}
    assert t.mcp_servers == ["zapier"]
    assert t.mcp_action_filter == {"zapier": ["gmail_find_email"]}
    assert t.memory == {"mode": "read-only", "scope": "research/"}
    assert t.shares == ["downloads-readable"]
    assert t.permission_overrides == {"terminal_run": "MUTATING"}
    assert t.timeout_seconds == 600
    assert t.max_tool_calls == 100

def test_bad_name_rejected(tmp_path):
    p = write(tmp_path, "weird", """
        name: WEB_Researcher
        model: m
        system_prompt: x
    """)
    with pytest.raises(TemplateLoadError, match="name must be lowercase-kebab"):
        load_template_file(p)

def test_name_mismatches_filename(tmp_path):
    p = write(tmp_path, "research-bot", """
        name: web-researcher
        model: m
        system_prompt: x
    """)
    with pytest.raises(TemplateLoadError, match="filename stem"):
        load_template_file(p)

def test_unknown_memory_mode(tmp_path):
    p = write(tmp_path, "x", """
        name: x
        model: m
        system_prompt: y
        memory:
          mode: read-write
    """)
    with pytest.raises(TemplateLoadError, match="not yet supported"):
        load_template_file(p)

def test_timeout_clamp(tmp_path):
    p = write(tmp_path, "x", """
        name: x
        model: m
        system_prompt: y
        timeout_seconds: 99999
    """)
    with pytest.raises(TemplateLoadError, match="timeout_seconds"):
        load_template_file(p)

def test_max_tool_calls_clamp(tmp_path):
    p = write(tmp_path, "x", """
        name: x
        model: m
        system_prompt: y
        max_tool_calls: 9999
    """)
    with pytest.raises(TemplateLoadError, match="max_tool_calls"):
        load_template_file(p)

def test_memory_scope_escape_rejected(tmp_path):
    p = write(tmp_path, "x", """
        name: x
        model: m
        system_prompt: y
        memory:
          mode: read-only
          scope: "../outside"
    """)
    with pytest.raises(TemplateLoadError, match="memory.scope"):
        load_template_file(p)
