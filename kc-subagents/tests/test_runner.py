from pathlib import Path
from kc_subagents.templates import SubagentTemplate
from kc_subagents.runner import template_to_agent_config

def test_template_to_agent_config_basic():
    t = SubagentTemplate(
        name="web-researcher", model="claude-opus-4-7",
        system_prompt="research things",
        tools={"web_search": {"budget": 20}, "skill_view": {}},
        timeout_seconds=300, max_tool_calls=30,
        source_path=Path("/tmp/web-researcher.yaml"),
    )
    cfg = template_to_agent_config(t, instance_id="ep_abc123", parent_agent="Kona-AI")
    assert cfg.name == "Kona-AI/ep_abc123/web-researcher"
    assert cfg.model == "claude-opus-4-7"
    assert cfg.system_prompt == "research things"
    assert set(cfg.tool_whitelist) == {"web_search", "skill_view"}
    assert cfg.tool_config == {"web_search": {"budget": 20}, "skill_view": {}}
