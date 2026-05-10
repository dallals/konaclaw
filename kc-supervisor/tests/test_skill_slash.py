from __future__ import annotations
from pathlib import Path

import pytest

from kc_skills import SkillIndex
from kc_supervisor.skill_slash import resolve_slash_command


def _seed_skill(tmp_path: Path, name: str, body: str = "Greet user.") -> SkillIndex:
    sdir = tmp_path / name
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: D\n---\n\n{body}\n"
    )
    return SkillIndex(tmp_path)


def test_known_skill_with_instruction(tmp_path):
    idx = _seed_skill(tmp_path, "hello", body="Greet politely.")
    out = resolve_slash_command("/hello set up SSH", skill_index=idx)
    assert out is not None
    loaded, instruction = out
    assert "[Skill activation: hello]" in loaded
    assert "Greet politely." in loaded
    assert "[Skill directory:" in loaded
    assert "The user's instruction is: set up SSH" in loaded
    assert instruction == "set up SSH"


def test_known_skill_without_instruction(tmp_path):
    idx = _seed_skill(tmp_path, "hello")
    out = resolve_slash_command("/hello", skill_index=idx)
    assert out is not None
    loaded, instruction = out
    assert "[Skill activation: hello]" in loaded
    assert "The user's instruction is:" not in loaded
    assert instruction == ""


def test_unknown_skill_returns_none(tmp_path):
    idx = _seed_skill(tmp_path, "hello")
    assert resolve_slash_command("/nope foo", skill_index=idx) is None


def test_plain_text_returns_none(tmp_path):
    idx = _seed_skill(tmp_path, "hello")
    assert resolve_slash_command("just talk to me", skill_index=idx) is None


def test_leading_whitespace_matches(tmp_path):
    idx = _seed_skill(tmp_path, "hello")
    out = resolve_slash_command("  /hello  foo", skill_index=idx)
    assert out is not None
    _, instruction = out
    assert instruction == "foo"


def test_wrong_case_does_not_match(tmp_path):
    idx = _seed_skill(tmp_path, "hello")
    assert resolve_slash_command("/Hello foo", skill_index=idx) is None


def test_slash_alone_does_not_match(tmp_path):
    idx = _seed_skill(tmp_path, "hello")
    assert resolve_slash_command("/", skill_index=idx) is None
    assert resolve_slash_command("/ foo", skill_index=idx) is None
