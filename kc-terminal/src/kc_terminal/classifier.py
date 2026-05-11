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


def classify_argv(argv: list[str]) -> RawTier:
    if not argv:
        raise BadArgvError("empty argv")
    cmd = _basename(argv[0])
    if cmd in DESTRUCTIVE_COMMANDS:
        return RawTier.DESTRUCTIVE
    if cmd in SAFE_COMMANDS:
        return RawTier.SAFE
    return RawTier.MUTATING


def classify_command(command: str) -> RawTier:
    raise NotImplementedError  # filled in Task 4
