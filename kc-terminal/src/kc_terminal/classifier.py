from __future__ import annotations
import shlex
from enum import Enum
from pathlib import PurePath


class RawTier(str, Enum):
    SAFE = "SAFE"
    MUTATING = "MUTATING"
    DESTRUCTIVE = "DESTRUCTIVE"


class BadArgvError(ValueError):
    pass


SAFE_COMMANDS = frozenset({
    "ls", "pwd", "cat", "head", "tail", "wc", "grep", "rg", "find", "file",
    "stat", "tree", "echo", "which", "whereis", "type", "env", "printenv",
    "date", "uname", "hostname", "uptime", "ps", "id", "true", "false",
})

DESTRUCTIVE_COMMANDS = frozenset({
    "rm", "rmdir", "mv", "cp",
    "sudo", "su", "doas",
    "kill", "killall", "pkill",
    "curl", "wget",
    "ssh", "scp", "rsync",
    "dd", "mkfs", "fdisk",
    "chmod", "chown", "chgrp",
    "shutdown", "reboot", "halt",
})


def _basename(arg0: str) -> str:
    return PurePath(arg0).name


GIT_SAFE_SUBCMDS = frozenset({
    "status", "log", "diff", "show", "blame", "branch", "remote",
    "rev-parse", "describe", "fetch", "ls-files",
})

GIT_DESTRUCTIVE_SUBCMDS = frozenset({
    "push", "filter-repo",
})

GH_SAFE_PAIRS = frozenset({
    ("repo", "view"),  ("repo", "list"),
    ("pr",   "view"),  ("pr",   "list"),  ("pr", "status"), ("pr", "diff"),
    ("issue","view"),  ("issue","list"),  ("issue", "status"),
    ("run",  "view"),  ("run",  "list"),
})

GH_DESTRUCTIVE_PAIRS = frozenset({
    ("pr", "merge"), ("pr", "close"),
    ("repo", "delete"),
    ("secret", "delete"), ("release", "delete"),
})


def _classify_git(argv: list[str]) -> RawTier:
    # argv[0] is "git" (already basenamed by caller).
    if len(argv) < 2:
        return RawTier.MUTATING
    sub = argv[1]
    if sub in GIT_DESTRUCTIVE_SUBCMDS:
        return RawTier.DESTRUCTIVE
    if sub in GIT_SAFE_SUBCMDS:
        # `git branch -D x` is destructive even though `branch` is in SAFE.
        if sub == "branch" and any(a == "-D" for a in argv[2:]):
            return RawTier.DESTRUCTIVE
        return RawTier.SAFE
    # `git reset --hard ...` -> destructive
    if sub == "reset" and any(a == "--hard" for a in argv[2:]):
        return RawTier.DESTRUCTIVE
    # `git clean -f` / `-fd` / `-fdx` / `--force` -> destructive
    if sub == "clean" and any(
        a == "--force" or (a.startswith("-") and not a.startswith("--") and "f" in a)
        for a in argv[2:]
    ):
        return RawTier.DESTRUCTIVE
    # `git tag -d x` / `--delete x` -> destructive
    if sub == "tag" and any(a in ("-d", "--delete") for a in argv[2:]):
        return RawTier.DESTRUCTIVE
    return RawTier.MUTATING


def _classify_gh(argv: list[str]) -> RawTier:
    if len(argv) < 3:
        return RawTier.MUTATING
    pair = (argv[1], argv[2])
    if pair in GH_DESTRUCTIVE_PAIRS:
        return RawTier.DESTRUCTIVE
    if pair in GH_SAFE_PAIRS:
        return RawTier.SAFE
    return RawTier.MUTATING


def classify_argv(argv: list[str]) -> RawTier:
    if not argv:
        raise BadArgvError("empty argv")
    cmd = _basename(argv[0])
    if cmd == "git":
        return _classify_git([cmd, *argv[1:]])
    if cmd == "gh":
        return _classify_gh([cmd, *argv[1:]])
    if cmd in DESTRUCTIVE_COMMANDS:
        return RawTier.DESTRUCTIVE
    if cmd in SAFE_COMMANDS:
        return RawTier.SAFE
    return RawTier.MUTATING


# Tokens that, on their own, mean "redirecting output to a real file" - DESTRUCTIVE.
_REDIRECT_TOKENS = frozenset({">", ">>", ">|"})

# Shell separators we split sub-commands on (so we can tier each segment).
_SEGMENT_SEPS = frozenset({"|", "||", "&&", ";", "&"})


def _split_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok in _SEGMENT_SEPS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def _segment_tier(segment: list[str]) -> RawTier:
    if not segment:
        return RawTier.MUTATING
    # Redirect operators inside a segment -> DESTRUCTIVE (unless to /dev/null).
    for i, tok in enumerate(segment):
        if tok in _REDIRECT_TOKENS:
            target = segment[i + 1] if i + 1 < len(segment) else ""
            if target != "/dev/null":
                return RawTier.DESTRUCTIVE
    # `tee` writing to a non-/dev/null path -> DESTRUCTIVE.
    if "tee" in segment:
        tee_idx = segment.index("tee")
        # Find first non-flag arg after tee.
        target = next(
            (a for a in segment[tee_idx + 1:] if not a.startswith("-")),
            None,
        )
        if target is not None and target != "/dev/null":
            return RawTier.DESTRUCTIVE
    # Any DESTRUCTIVE command token in the segment.
    for tok in segment:
        cmd = _basename(tok)
        if cmd in DESTRUCTIVE_COMMANDS:
            return RawTier.DESTRUCTIVE
    # Adjacent `git push` -> DESTRUCTIVE.
    for i in range(len(segment) - 1):
        if _basename(segment[i]) == "git" and segment[i + 1] == "push":
            return RawTier.DESTRUCTIVE
    # Shell mode is never SAFE.
    return RawTier.MUTATING


def classify_command(command: str) -> RawTier:
    if not command or not command.strip():
        raise BadArgvError("empty command")
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as e:
        # Unterminated quote etc. Be conservative.
        raise BadArgvError(f"unparseable command: {e}") from e
    segments = _split_segments(tokens)
    tier = RawTier.MUTATING
    for seg in segments:
        seg_tier = _segment_tier(seg)
        if seg_tier == RawTier.DESTRUCTIVE:
            return RawTier.DESTRUCTIVE
        if seg_tier == RawTier.MUTATING:
            tier = RawTier.MUTATING
    return tier
