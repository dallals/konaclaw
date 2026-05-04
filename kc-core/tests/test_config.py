import warnings
from pathlib import Path
import pytest
from kc_core.config import AgentConfig, load_agent_config


FIXTURE = Path(__file__).parent / "fixtures" / "agents" / "echo.yaml"


def test_load_agent_config_returns_config():
    cfg = load_agent_config(FIXTURE)
    assert isinstance(cfg, AgentConfig)
    assert cfg.name == "echo-agent"
    assert cfg.model == "gemma3:4b"
    assert "echo-agent" in cfg.system_prompt


def test_unknown_keys_warn_but_load(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text("name: x\nmodel: m\nsystem_prompt: hi\nfuture_field: 42\n")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cfg = load_agent_config(p)
    assert cfg.name == "x"
    assert any("future_field" in str(x.message) for x in w)


def test_missing_required_field_raises(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text("model: m\nsystem_prompt: hi\n")
    with pytest.raises(ValueError, match="name"):
        load_agent_config(p)


def test_default_model_when_omitted(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text("name: x\nsystem_prompt: hi\n")
    cfg = load_agent_config(p, default_model="qwen2.5:32b")
    assert cfg.model == "qwen2.5:32b"
