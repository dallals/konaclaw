from __future__ import annotations
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


def classify_command(command: str) -> RawTier:
    raise NotImplementedError  # filled in Task 4
