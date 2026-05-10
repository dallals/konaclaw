from __future__ import annotations
import subprocess
import sys
from pathlib import Path


def test_channel_routing_add_writes_row(tmp_path: Path):
    db = tmp_path / "kc.db"
    from kc_supervisor.storage import Storage
    Storage(db).init()
    result = subprocess.run(
        [sys.executable, "-m", "kc_supervisor", "channel-routing", "add",
         "--db", str(db), "telegram", "8627206839"],
        capture_output=True, text=True,
        cwd="/Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor",
    )
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    routing = Storage(db).get_channel_routing("telegram")
    assert routing == {"default_chat_id": "8627206839", "enabled": 1}


def test_channel_routing_list_prints_entries(tmp_path: Path):
    db = tmp_path / "kc.db"
    from kc_supervisor.storage import Storage
    s = Storage(db); s.init()
    s.upsert_channel_routing("telegram", "T", enabled=1)
    s.upsert_channel_routing("imessage", "I", enabled=0)
    result = subprocess.run(
        [sys.executable, "-m", "kc_supervisor", "channel-routing", "list", "--db", str(db)],
        capture_output=True, text=True,
        cwd="/Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor",
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "telegram" in result.stdout
    assert "imessage" in result.stdout


def test_channel_routing_disable_flips_enabled(tmp_path: Path):
    db = tmp_path / "kc.db"
    from kc_supervisor.storage import Storage
    s = Storage(db); s.init()
    s.upsert_channel_routing("telegram", "T", enabled=1)
    result = subprocess.run(
        [sys.executable, "-m", "kc_supervisor", "channel-routing", "disable",
         "--db", str(db), "telegram"],
        capture_output=True, text=True,
        cwd="/Users/sammydallal/Desktop/claudeCode/SammyClaw/kc-supervisor",
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    routing = Storage(db).get_channel_routing("telegram")
    assert routing["enabled"] == 0
