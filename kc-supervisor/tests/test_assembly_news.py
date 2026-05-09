from __future__ import annotations

from unittest.mock import MagicMock

import yaml
from kc_sandbox.permissions import Tier
from kc_supervisor.assembly import assemble_agent
from kc_supervisor.agents import load_agent_config


def _agent_yaml(tmp_path):
    p = tmp_path / "agents" / "kona.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({
        "name": "kona", "model": "fake", "system_prompt": "hi",
    }))
    return p


def _build(tmp_path, *, news_client):
    from kc_sandbox.shares import SharesRegistry
    from kc_supervisor.approvals import ApprovalBroker
    from kc_supervisor.storage import Storage

    home = tmp_path / "kc"
    (home / "data").mkdir(parents=True)
    (home / "shares" / "main").mkdir(parents=True)
    (home / "config").mkdir(parents=True)
    (home / "config" / "shares.yaml").write_text(yaml.safe_dump({
        "shares": [{"name": "main", "path": str(home / "shares" / "main"), "mode": "read-write"}],
    }))
    storage = Storage(home / "data" / "kc.db"); storage.init()
    cfg_path = _agent_yaml(home)
    cfg = load_agent_config(cfg_path, default_model="fake")
    return assemble_agent(
        cfg=cfg,
        shares=SharesRegistry.from_yaml(home / "config" / "shares.yaml"),
        audit_storage=storage,
        broker=ApprovalBroker(),
        ollama_url="http://localhost:11434",
        default_model="fake",
        undo_db_path=home / "data" / "undo.db",
        news_client=news_client,
    )


def test_news_tools_registered_when_client_present(tmp_path):
    client = MagicMock(name="NewsClient")
    a = _build(tmp_path, news_client=client)
    names = set(a.registry.names())
    assert "news.search_topic" in names
    assert "news.from_source" in names


def test_news_tools_absent_when_client_none(tmp_path):
    a = _build(tmp_path, news_client=None)
    names = set(a.registry.names())
    assert "news.search_topic" not in names
    assert "news.from_source" not in names


def test_news_tools_are_safe_tier(tmp_path):
    client = MagicMock(name="NewsClient")
    a = _build(tmp_path, news_client=client)
    assert a.engine.tier_map["news.search_topic"] == Tier.SAFE
    assert a.engine.tier_map["news.from_source"] == Tier.SAFE
