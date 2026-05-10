from __future__ import annotations
import sys
from kc_skills.frontmatter import (
    parse_skill_frontmatter,
    skill_matches_platform,
    FrontmatterError,
)
import pytest


VALID = """---
name: hello-world
description: Greet the user politely.
version: 1.0.0
tags: [greetings, demo]
---

# Hello

Body text.
"""


def test_parse_valid_frontmatter():
    fm, body = parse_skill_frontmatter(VALID)
    assert fm["name"] == "hello-world"
    assert fm["description"] == "Greet the user politely."
    assert fm["version"] == "1.0.0"
    assert fm["tags"] == ["greetings", "demo"]
    assert body.strip().startswith("# Hello")


def test_missing_opening_dashes_raises():
    with pytest.raises(FrontmatterError, match="missing"):
        parse_skill_frontmatter("# No frontmatter\n\nBody")


def test_missing_closing_dashes_raises():
    with pytest.raises(FrontmatterError, match="closing"):
        parse_skill_frontmatter("---\nname: foo\ndescription: bar\n# no close")


def test_missing_name_raises():
    with pytest.raises(FrontmatterError, match="name"):
        parse_skill_frontmatter("---\ndescription: x\n---\nbody")


def test_missing_description_does_not_raise():
    # Skill is included with description="(missing)" per spec.
    fm, _ = parse_skill_frontmatter("---\nname: foo\n---\nbody")
    assert fm["name"] == "foo"
    assert fm["description"] == "(missing)"


def test_invalid_name_chars_raises():
    with pytest.raises(FrontmatterError, match="name"):
        parse_skill_frontmatter("---\nname: Has Spaces\ndescription: x\n---\nbody")


def test_name_too_long_raises():
    long_name = "a" * 65
    with pytest.raises(FrontmatterError, match="name"):
        parse_skill_frontmatter(f"---\nname: {long_name}\ndescription: x\n---\nbody")


def test_description_too_long_raises():
    long_desc = "x" * 1025
    with pytest.raises(FrontmatterError, match="description"):
        parse_skill_frontmatter(f"---\nname: foo\ndescription: {long_desc}\n---\nbody")


def test_bad_yaml_raises():
    with pytest.raises(FrontmatterError, match="YAML"):
        parse_skill_frontmatter("---\nname: [: : :\n---\nbody")


def test_platform_match_current_os():
    # Don't mock sys.platform — just use the real one.
    fm = {"platforms": [_current_skill_platform()]}
    assert skill_matches_platform(fm) is True


def test_platform_no_field_matches_all():
    assert skill_matches_platform({}) is True
    assert skill_matches_platform({"platforms": None}) is True
    assert skill_matches_platform({"platforms": []}) is True


def test_platform_string_form_matches():
    fm = {"platforms": _current_skill_platform()}
    assert skill_matches_platform(fm) is True


def test_platform_other_os_does_not_match():
    other = "windows" if sys.platform == "darwin" else "macos"
    assert skill_matches_platform({"platforms": [other]}) is False


def _current_skill_platform() -> str:
    if sys.platform.startswith("darwin"):
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform.startswith("win"):
        return "windows"
    return sys.platform
