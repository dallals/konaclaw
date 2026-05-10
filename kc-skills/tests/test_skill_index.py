from __future__ import annotations
import os
import textwrap
import time
from pathlib import Path

import pytest

from kc_skills.skill_index import SkillIndex, PathOutsideSkillDir


def _write_skill(
    root: Path,
    *,
    name: str,
    category: str | None = None,
    description: str = "test description",
    extras: str = "",
    body: str = "Body text.",
) -> Path:
    """Write a SKILL.md under root and return its path."""
    if category:
        dir_ = root / category / name
    else:
        dir_ = root / name
    dir_.mkdir(parents=True, exist_ok=True)
    fm = f"---\nname: {name}\ndescription: {description}\n{extras}---\n\n{body}\n"
    p = dir_ / "SKILL.md"
    p.write_text(fm)
    return p


def test_empty_dir(tmp_path):
    idx = SkillIndex(tmp_path)
    assert idx.list() == []
    assert idx.get("anything") is None


def test_missing_root_returns_empty(tmp_path):
    idx = SkillIndex(tmp_path / "does-not-exist")
    assert idx.list() == []


def test_single_skill_flat_layout(tmp_path):
    _write_skill(tmp_path, name="hello-world", description="Greet politely.")
    idx = SkillIndex(tmp_path)
    summaries = idx.list()
    assert len(summaries) == 1
    s = summaries[0]
    assert s.name == "hello-world"
    assert s.category is None
    assert s.description == "Greet politely."
    assert s.skill_dir == tmp_path / "hello-world"


def test_single_skill_with_category(tmp_path):
    _write_skill(tmp_path, name="hello", category="greetings", description="Hi.")
    idx = SkillIndex(tmp_path)
    summaries = idx.list()
    assert len(summaries) == 1
    assert summaries[0].name == "hello"
    assert summaries[0].category == "greetings"
    assert summaries[0].skill_dir == tmp_path / "greetings" / "hello"


def test_multiple_categories_alphabetical(tmp_path):
    _write_skill(tmp_path, name="b-skill", category="alpha")
    _write_skill(tmp_path, name="a-skill", category="alpha")
    _write_skill(tmp_path, name="z-skill", category="beta")
    idx = SkillIndex(tmp_path)
    names = [s.name for s in idx.list()]
    # Category alpha first, then beta. Within alpha: a-skill before b-skill.
    assert names == ["a-skill", "b-skill", "z-skill"]


def test_get_returns_full_skill_with_body(tmp_path):
    _write_skill(tmp_path, name="hello", body="# Hello\n\nLong body.")
    idx = SkillIndex(tmp_path)
    skill = idx.get("hello")
    assert skill is not None
    assert skill.summary.name == "hello"
    assert "# Hello" in skill.body
    assert "Long body." in skill.body


def test_get_unknown_returns_none(tmp_path):
    idx = SkillIndex(tmp_path)
    assert idx.get("nope") is None


def test_get_includes_supporting_files(tmp_path):
    _write_skill(tmp_path, name="hello", category="greetings")
    sdir = tmp_path / "greetings" / "hello"
    (sdir / "references").mkdir()
    (sdir / "references" / "doc.md").write_text("ref body")
    (sdir / "templates").mkdir()
    (sdir / "templates" / "tpl.txt").write_text("tpl body")
    (sdir / "scripts").mkdir()
    (sdir / "scripts" / "run.sh").write_text("#!/bin/sh\necho hi\n")

    idx = SkillIndex(tmp_path)
    skill = idx.get("hello")
    assert skill is not None
    sf = skill.supporting_files
    assert sf["references"] == ["doc.md"]
    assert sf["templates"] == ["tpl.txt"]
    assert sf["scripts"] == ["run.sh"]
    assert sf["assets"] == []


def test_read_supporting_file_happy(tmp_path):
    _write_skill(tmp_path, name="hello")
    sdir = tmp_path / "hello"
    (sdir / "references").mkdir()
    (sdir / "references" / "doc.md").write_text("ref body")

    idx = SkillIndex(tmp_path)
    assert idx.read_supporting_file("hello", "references/doc.md") == "ref body"


def test_read_supporting_file_missing_returns_none(tmp_path):
    _write_skill(tmp_path, name="hello")
    idx = SkillIndex(tmp_path)
    assert idx.read_supporting_file("hello", "references/nope.md") is None


def test_read_supporting_file_path_escape_raises(tmp_path):
    _write_skill(tmp_path, name="hello")
    (tmp_path / "secret.txt").write_text("don't read me")
    idx = SkillIndex(tmp_path)
    with pytest.raises(PathOutsideSkillDir):
        idx.read_supporting_file("hello", "../secret.txt")


def test_read_supporting_file_absolute_path_raises(tmp_path):
    _write_skill(tmp_path, name="hello")
    idx = SkillIndex(tmp_path)
    with pytest.raises(PathOutsideSkillDir):
        idx.read_supporting_file("hello", "/etc/passwd")


def test_script_path_happy(tmp_path):
    _write_skill(tmp_path, name="hello")
    (tmp_path / "hello" / "scripts").mkdir()
    sp = tmp_path / "hello" / "scripts" / "run.sh"
    sp.write_text("#!/bin/sh\necho hi\n")

    idx = SkillIndex(tmp_path)
    got = idx.script_path("hello", "run.sh")
    assert got == sp.resolve()


def test_script_path_missing_returns_none(tmp_path):
    _write_skill(tmp_path, name="hello")
    idx = SkillIndex(tmp_path)
    assert idx.script_path("hello", "nope.sh") is None


def test_script_path_escape_raises(tmp_path):
    _write_skill(tmp_path, name="hello")
    (tmp_path / "hello" / "scripts").mkdir()
    idx = SkillIndex(tmp_path)
    with pytest.raises(PathOutsideSkillDir):
        idx.script_path("hello", "../../etc/passwd")


def test_mtime_invalidation_picks_up_edits(tmp_path):
    _write_skill(tmp_path, name="hello", description="v1")
    idx = SkillIndex(tmp_path)
    assert idx.list()[0].description == "v1"

    # Edit and bump mtime explicitly so the test isn't flaky on filesystems
    # whose mtime resolution is coarse.
    p = tmp_path / "hello" / "SKILL.md"
    p.write_text(p.read_text().replace("v1", "v2"))
    future = time.time() + 10
    os.utime(p, (future, future))

    summaries = idx.list()
    assert summaries[0].description == "v2"


def test_mtime_invalidation_drops_deleted(tmp_path):
    _write_skill(tmp_path, name="hello")
    _write_skill(tmp_path, name="goodbye")
    idx = SkillIndex(tmp_path)
    assert sorted(s.name for s in idx.list()) == ["goodbye", "hello"]

    import shutil
    shutil.rmtree(tmp_path / "goodbye")
    assert [s.name for s in idx.list()] == ["hello"]


def test_duplicate_name_first_wins(tmp_path, caplog):
    import logging
    caplog.set_level(logging.WARNING)
    _write_skill(tmp_path, name="hello", category="alpha", description="alpha hello")
    _write_skill(tmp_path, name="hello", category="beta", description="beta hello")
    idx = SkillIndex(tmp_path)
    summaries = idx.list()
    assert len(summaries) == 1
    assert summaries[0].category == "alpha"
    assert summaries[0].description == "alpha hello"
    assert any("duplicate" in r.message.lower() for r in caplog.records)


def test_bad_yaml_skipped_others_indexed(tmp_path, caplog):
    import logging
    caplog.set_level(logging.WARNING)
    _write_skill(tmp_path, name="good")
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "SKILL.md").write_text("---\nname: [: : :\n---\nbody")
    idx = SkillIndex(tmp_path)
    names = [s.name for s in idx.list()]
    assert names == ["good"]
    assert any("bad" in r.message for r in caplog.records)


def test_platforms_filter_excludes_other_os(tmp_path):
    import sys
    other = "windows" if sys.platform == "darwin" else "macos"
    _write_skill(
        tmp_path, name="excluded",
        extras=f"platforms: [{other}]\n",
    )
    _write_skill(tmp_path, name="included")
    idx = SkillIndex(tmp_path)
    names = [s.name for s in idx.list()]
    assert names == ["included"]
