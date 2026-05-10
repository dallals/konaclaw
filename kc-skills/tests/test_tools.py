from __future__ import annotations
import asyncio
import json
from pathlib import Path

import pytest

from kc_skills import build_skill_tools, SkillIndex


def _write_skill(root: Path, name: str, body: str = "Hello body", **fm) -> None:
    sdir = root / name
    sdir.mkdir(parents=True, exist_ok=True)
    extras = "".join(f"{k}: {v}\n" for k, v in fm.items())
    p = sdir / "SKILL.md"
    p.write_text(f"---\nname: {name}\ndescription: D\n{extras}---\n\n{body}\n")


def _build(root: Path):
    idx = SkillIndex(root)
    return idx, {t.name: t for t in build_skill_tools(skill_index=idx)}


def _call(tool, **kwargs) -> dict:
    """Invoke a tool's impl (sync or async) and parse JSON."""
    result = tool.impl(**kwargs)
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    return json.loads(result)


def test_skills_list_empty(tmp_path):
    _, tools = _build(tmp_path)
    out = _call(tools["skills_list"])
    assert out["skills"] == []
    assert out["count"] == 0
    assert "No skills" in out.get("message", "")


def test_skills_list_returns_minimal_metadata(tmp_path):
    _write_skill(tmp_path, "hello")
    _write_skill(tmp_path, "goodbye")
    _, tools = _build(tmp_path)
    out = _call(tools["skills_list"])
    assert out["count"] == 2
    keys = set(out["skills"][0].keys())
    assert keys == {"name", "category", "description"}


def test_skills_list_filter_by_category(tmp_path):
    sdir = tmp_path / "alpha" / "a-skill"
    sdir.mkdir(parents=True)
    (sdir / "SKILL.md").write_text("---\nname: a-skill\ndescription: D\n---\nbody")
    _write_skill(tmp_path, "flat-skill")

    _, tools = _build(tmp_path)
    out = _call(tools["skills_list"], category="alpha")
    names = [s["name"] for s in out["skills"]]
    assert names == ["a-skill"]


def test_skill_view_full(tmp_path):
    _write_skill(tmp_path, "hello", body="# Heading\n\nLong text.")
    _, tools = _build(tmp_path)
    out = _call(tools["skill_view"], name="hello")
    assert out["name"] == "hello"
    assert "# Heading" in out["content"]
    assert "supporting_files" in out
    assert set(out["supporting_files"].keys()) == {"references", "templates", "assets", "scripts"}
    assert "skill_dir" in out


def test_skill_view_supporting_file(tmp_path):
    _write_skill(tmp_path, "hello")
    sdir = tmp_path / "hello"
    (sdir / "references").mkdir()
    (sdir / "references" / "doc.md").write_text("ref body")

    _, tools = _build(tmp_path)
    out = _call(tools["skill_view"], name="hello", file_path="references/doc.md")
    assert out["file_path"] == "references/doc.md"
    assert out["content"] == "ref body"


def test_skill_view_unknown_skill(tmp_path):
    _, tools = _build(tmp_path)
    out = _call(tools["skill_view"], name="nope")
    assert out["error"] == "skill_not_found"


def test_skill_view_unknown_file(tmp_path):
    _write_skill(tmp_path, "hello")
    _, tools = _build(tmp_path)
    out = _call(tools["skill_view"], name="hello", file_path="nope.md")
    assert out["error"] == "file_not_found"


def test_skill_view_path_escape(tmp_path):
    _write_skill(tmp_path, "hello")
    (tmp_path / "secret.txt").write_text("nope")
    _, tools = _build(tmp_path)
    out = _call(tools["skill_view"], name="hello", file_path="../secret.txt")
    assert out["error"] == "path_outside_skill_dir"


def test_skill_run_script_happy(tmp_path):
    _write_skill(tmp_path, "hello")
    sdir = tmp_path / "hello"
    (sdir / "scripts").mkdir()
    sp = sdir / "scripts" / "echo.sh"
    sp.write_text("#!/bin/sh\necho hi from $1\n")
    sp.chmod(0o755)

    _, tools = _build(tmp_path)
    out = _call(tools["skill_run_script"], name="hello", script="echo.sh", args=["sammy"])
    assert out["exit_code"] == 0
    assert "hi from sammy" in out["stdout"]
    assert out["stderr"] == ""


def test_skill_run_script_unknown_skill(tmp_path):
    _, tools = _build(tmp_path)
    out = _call(tools["skill_run_script"], name="nope", script="x.sh")
    assert out["error"] == "skill_not_found"


def test_skill_run_script_missing(tmp_path):
    _write_skill(tmp_path, "hello")
    _, tools = _build(tmp_path)
    out = _call(tools["skill_run_script"], name="hello", script="nope.sh")
    assert out["error"] == "script_not_found"


def test_skill_run_script_path_escape(tmp_path):
    _write_skill(tmp_path, "hello")
    _, tools = _build(tmp_path)
    out = _call(tools["skill_run_script"], name="hello", script="../../etc/passwd")
    assert out["error"] == "path_outside_skill_dir"


def test_skill_run_script_not_executable(tmp_path):
    _write_skill(tmp_path, "hello")
    sdir = tmp_path / "hello"
    (sdir / "scripts").mkdir()
    sp = sdir / "scripts" / "noexec.sh"
    sp.write_text("#!/bin/sh\necho hi\n")
    sp.chmod(0o644)  # not executable

    _, tools = _build(tmp_path)
    out = _call(tools["skill_run_script"], name="hello", script="noexec.sh")
    assert out["error"] == "not_executable"


def test_skill_run_script_timeout(tmp_path, monkeypatch):
    _write_skill(tmp_path, "hello")
    sdir = tmp_path / "hello"
    (sdir / "scripts").mkdir()
    sp = sdir / "scripts" / "slow.sh"
    sp.write_text("#!/bin/sh\nsleep 5\n")
    sp.chmod(0o755)

    # Tighten the timeout for this test.
    monkeypatch.setenv("KC_SKILL_SCRIPT_TIMEOUT", "1")

    _, tools = _build(tmp_path)
    out = _call(tools["skill_run_script"], name="hello", script="slow.sh")
    assert out["error"] == "timeout"


def test_skill_run_script_output_truncation(tmp_path):
    _write_skill(tmp_path, "hello")
    sdir = tmp_path / "hello"
    (sdir / "scripts").mkdir()
    sp = sdir / "scripts" / "loud.sh"
    # 20 KB of output (over the 16 KB cap).
    sp.write_text("#!/bin/sh\nyes x | head -c 20480\n")
    sp.chmod(0o755)

    _, tools = _build(tmp_path)
    out = _call(tools["skill_run_script"], name="hello", script="loud.sh")
    assert out["exit_code"] == 0
    assert out["stdout_truncated"] is True
    assert len(out["stdout"]) <= 16384
