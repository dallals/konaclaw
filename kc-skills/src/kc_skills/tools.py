"""Skill tools — agent-facing surface backed by SkillIndex.

Returns three Tool objects:

  - skills_list(category=None) -> str (JSON)
  - skill_view(name, file_path=None) -> str (JSON)
  - skill_run_script(name, script, args=None) -> str (JSON)

Each tool returns a JSON string so the kc-core agent can hand the result
back to the model verbatim. Errors are returned as JSON `{"error": ...}`
rather than raised exceptions — the agent decides what to do next.

The skill_run_script tool runs subprocesses synchronously. It does NOT
call ApprovalBroker directly: the supervisor registers it as
Tier.DESTRUCTIVE in assembly.py, so the existing PermissionEngine + broker
chain prompts before each call. Denial returns a tool error to the agent
through the engine, never reaching this module.
"""
from __future__ import annotations
import json
import os
import subprocess
import time
from typing import Any, Optional

from kc_core.tools import Tool
from kc_skills.skill_index import PathOutsideSkillDir, SkillIndex


_OUTPUT_CAP_BYTES = 16 * 1024
_DEFAULT_TIMEOUT_SECONDS = 120


def build_skill_tools(*, skill_index: SkillIndex) -> list[Tool]:
    """Build the three skill tools bound to one SkillIndex.

    Tools are pure-impl — no closure over agent name or broker. The
    supervisor handles approval routing via Tier.DESTRUCTIVE wiring.
    """
    return [
        _build_skills_list(skill_index),
        _build_skill_view(skill_index),
        _build_skill_run_script(skill_index),
    ]


def _build_skills_list(idx: SkillIndex) -> Tool:
    def impl(category: Optional[str] = None) -> str:
        summaries = idx.list()
        if category is not None:
            summaries = [s for s in summaries if s.category == category]
        skills = [
            {"name": s.name, "category": s.category, "description": s.description}
            for s in summaries
        ]
        if not skills:
            return json.dumps({
                "skills": [],
                "categories": [],
                "count": 0,
                "message": "No skills found in ~/KonaClaw/skills/",
            })
        categories = sorted({s["category"] for s in skills if s["category"]})
        return json.dumps({
            "skills": skills,
            "categories": categories,
            "count": len(skills),
            "hint": "Use skill_view(name) to load a skill's full instructions.",
        })

    return Tool(
        name="skills_list",
        description=(
            "List available skills (name, category, description). "
            "Skills are task-focused capability modules that can be loaded "
            "on demand with skill_view(name). Use this when the user asks "
            "for help with a domain you might have a skill for."
        ),
        parameters={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional category filter (e.g. 'github').",
                },
            },
            "required": [],
        },
        impl=impl,
    )


def _build_skill_view(idx: SkillIndex) -> Tool:
    def impl(name: str, file_path: Optional[str] = None) -> str:
        skill = idx.get(name)
        if skill is None:
            return json.dumps({"error": "skill_not_found", "name": name})
        if file_path is None:
            return json.dumps({
                "name": skill.summary.name,
                "category": skill.summary.category,
                "skill_dir": str(skill.summary.skill_dir),
                "frontmatter": {
                    "version": skill.summary.version,
                    "tags": skill.summary.tags,
                    "platforms": skill.summary.platforms,
                    "related_skills": skill.summary.related_skills,
                },
                "content": skill.body,
                "supporting_files": skill.supporting_files,
                "hint": (
                    "Load a supporting file with "
                    "skill_view(name, file_path=\"references/foo.md\")."
                ),
            })
        # Supporting file mode.
        try:
            content = idx.read_supporting_file(name, file_path)
        except PathOutsideSkillDir:
            return json.dumps({"error": "path_outside_skill_dir"})
        if content is None:
            return json.dumps({"error": "file_not_found", "file_path": file_path})
        return json.dumps({
            "name": skill.summary.name,
            "file_path": file_path,
            "content": content,
        })

    return Tool(
        name="skill_view",
        description=(
            "Load a skill's full instructions (content) and the manifest of "
            "its supporting files. Pass file_path to load one of those files "
            "(e.g. file_path='references/foo.md'). Use this after skills_list "
            "identifies a skill that fits the task."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name."},
                "file_path": {
                    "type": "string",
                    "description": (
                        "Optional. Relative path to a supporting file "
                        "inside the skill dir."
                    ),
                },
            },
            "required": ["name"],
        },
        impl=impl,
    )


def _build_skill_run_script(idx: SkillIndex) -> Tool:
    def impl(name: str, script: str, args: Optional[list[str]] = None) -> str:
        if idx.get(name) is None:
            return json.dumps({"error": "skill_not_found", "name": name})

        # Defensive: some models pass the whole CLI as the `script` field
        # (e.g. "python3 portfolio.py --silent" or "portfolio.py --silent")
        # instead of separating script + args. Try to recover by splitting
        # on whitespace; if a real script in the skill matches one of the
        # tokens, treat that as the script and the remainder as args.
        import shlex as _shlex
        if " " in script.strip():
            try:
                tokens = _shlex.split(script)
            except ValueError:
                tokens = script.split()
            if tokens:
                # Drop a leading "python3" / "python" / "bash" / "sh"
                # interpreter token — the script's shebang handles that.
                if tokens[0] in ("python3", "python", "bash", "sh"):
                    tokens = tokens[1:]
                if tokens:
                    cand = idx.script_path(name, tokens[0]) if tokens else None
                    if cand is not None:
                        script = tokens[0]
                        extra = tokens[1:]
                        args = (list(args) if args else []) + extra

        try:
            sp = idx.script_path(name, script)
        except PathOutsideSkillDir:
            return json.dumps({"error": "path_outside_skill_dir"})
        if sp is None:
            return json.dumps({
                "error": "script_not_found",
                "script": script,
                "hint": (
                    "Pass JUST the filename in `script` (e.g. 'portfolio.py'). "
                    "Put flags and arguments in `args` (e.g. ['--silent']). "
                    "Do NOT include 'python3' or other interpreters — the "
                    "script's shebang handles that."
                ),
            })
        if not os.access(sp, os.X_OK):
            return json.dumps({"error": "not_executable", "script": script})

        timeout = _resolve_timeout()
        skill_dir = sp.parent.parent  # scripts/<name> -> skill_dir
        env = {**os.environ, "KC_SKILL_DIR": str(skill_dir)}
        argv = [str(sp)] + list(args or [])

        start_ns = time.time_ns()
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(skill_dir),
                env=env,
            )
        except subprocess.TimeoutExpired as e:
            duration_ms = (time.time_ns() - start_ns) // 1_000_000
            return json.dumps({
                "error": "timeout",
                "duration_ms": duration_ms,
                "stdout": _truncate(e.stdout or ""),
                "stderr": _truncate(e.stderr or ""),
            })

        duration_ms = (time.time_ns() - start_ns) // 1_000_000
        stdout, stdout_truncated = _truncate_with_flag(completed.stdout or "")
        stderr, stderr_truncated = _truncate_with_flag(completed.stderr or "")
        return json.dumps({
            "name": name,
            "script": script,
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stdout_truncated": stdout_truncated,
            "stderr": stderr,
            "stderr_truncated": stderr_truncated,
            "duration_ms": duration_ms,
        })

    return Tool(
        name="skill_run_script",
        description=(
            "Run a script bundled with a skill (under <skill_dir>/scripts/). "
            "Returns exit_code, stdout, stderr, duration_ms. Each call "
            "prompts the user for approval. Use only when the skill's "
            "instructions explicitly direct you to."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name."},
                "script": {
                    "type": "string",
                    "description": (
                        "JUST the script filename inside the skill's scripts/ dir "
                        "(e.g. 'portfolio.py'). Never include 'python3' or other "
                        "interpreter prefixes — the script's shebang handles that. "
                        "Never include CLI flags here; put them in `args` instead."
                    ),
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "CLI flags and positional arguments, one per item. "
                        "Example: ['--silent'] or ['--retire-age', '58']. "
                        "Each flag and value goes in its OWN array slot."
                    ),
                },
            },
            "required": ["name", "script"],
        },
        impl=impl,
    )


def _resolve_timeout() -> int:
    raw = os.environ.get("KC_SKILL_SCRIPT_TIMEOUT")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return _DEFAULT_TIMEOUT_SECONDS


def _truncate(text: str) -> str:
    return text if len(text.encode("utf-8")) <= _OUTPUT_CAP_BYTES else text[: _OUTPUT_CAP_BYTES // 2]


def _truncate_with_flag(text: str) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= _OUTPUT_CAP_BYTES:
        return text, False
    # Truncate at a UTF-8 safe boundary by re-decoding the first N bytes.
    return encoded[: _OUTPUT_CAP_BYTES].decode("utf-8", errors="ignore"), True
