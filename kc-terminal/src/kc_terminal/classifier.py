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
_REDIRECT_TOKENS = frozenset({
    ">", ">>", ">|",
    "1>", "1>>",
    "2>", "2>>",
    "&>", "&>>",
    "<>",
})

# Shell separators we split sub-commands on (so we can tier each segment).
_SEGMENT_SEPS = frozenset({"|", "||", "&&", ";", "&"})

# Tools whose first non-flag arg is itself a command to run. We recurse into them.
_WRAPPER_COMMANDS = frozenset({
    "xargs", "nohup", "time", "nice", "exec", "command",
})

# Shells. We recursively classify their `-c PAYLOAD` argument as a shell command.
_SHELL_RUNNERS = frozenset({
    "bash", "sh", "zsh", "dash", "ksh", "fish",
})

# Language interpreters. If invoked with -c / -e, they execute arbitrary code.
_LANG_RUNNERS = frozenset({
    "python", "python3", "node", "perl", "ruby",
})


def _looks_like_env_assignment(token: str) -> bool:
    """Recognize VAR=value or VAR_NAME=value tokens used to set env before a command."""
    if "=" not in token or token.startswith("-") or token.startswith("="):
        return False
    name = token.split("=", 1)[0]
    if not name:
        return False
    # Env var names: letters, digits, underscores; first char not a digit.
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in name)


# Per-wrapper: which flags take a separate VALUE token. We skip both flag and value
# when finding the command position. Conservative — if a wrapper's flag isn't listed
# here we treat it as bare (no value).
_WRAPPER_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "nice":  frozenset({"-n"}),
    "xargs": frozenset({"-P", "-n", "-I", "-L", "-a", "-d", "-E", "-s"}),
    # `time`, `nohup`, `exec`, `command` don't take value-flags in their common forms.
    "time":  frozenset(),
    "nohup": frozenset(),
    "exec":  frozenset(),
    "command": frozenset(),
}


def _strip_wrapper_flags(wrapper: str, tokens: list[str]) -> list[str]:
    """For a wrapper command (xargs/nohup/time/nice/exec/command), return the
    tokens starting at the wrapped command's position. Skips leading flags,
    consuming a value-token after any flag in _WRAPPER_VALUE_FLAGS[wrapper]."""
    value_flags = _WRAPPER_VALUE_FLAGS.get(wrapper, frozenset())
    i = 0
    while i < len(tokens) and tokens[i].startswith("-"):
        # `--` terminates flag parsing.
        if tokens[i] == "--":
            i += 1
            break
        flag = tokens[i]
        i += 1
        # Long-form `--flag=value` self-contained -- no extra consume.
        if flag.startswith("--") and "=" in flag:
            continue
        # If this is a value-taking flag (and we're not already done), consume next.
        if flag in value_flags and i < len(tokens):
            i += 1
    return tokens[i:]


def _looks_like_command_name(tok: str) -> bool:
    """True if `tok` plausibly is a command name (not a number, not empty,
    not a flag-leftover). Conservative: a command must start with a letter
    or underscore and contain only command-name-safe chars."""
    if not tok:
        return False
    if not (tok[0].isalpha() or tok[0] == "_"):
        return False
    # Allow letters, digits, hyphens, underscores, dots (e.g. `python3.11`).
    return all(c.isalnum() or c in "-_." for c in tok)


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

    # Token-level escalations (apply regardless of command position).
    # 1. Backticks or $(...) -- can't safely classify the substitution, escalate.
    for tok in segment:
        if tok.startswith("`") or tok.startswith("$("):
            return RawTier.DESTRUCTIVE
    # 2. Redirect operators with non-/dev/null target.
    for i, tok in enumerate(segment):
        if tok in _REDIRECT_TOKENS:
            target = segment[i + 1] if i + 1 < len(segment) else ""
            if target != "/dev/null":
                return RawTier.DESTRUCTIVE

    # Find the command position: skip leading FOO=bar env assignments.
    cmd_idx = 0
    while cmd_idx < len(segment) and _looks_like_env_assignment(segment[cmd_idx]):
        cmd_idx += 1
    if cmd_idx >= len(segment):
        return RawTier.MUTATING

    cmd_tok = _basename(segment[cmd_idx])
    rest = segment[cmd_idx + 1:]

    # Wrapper recursion -- the wrapper itself is benign; classify the wrapped command.
    if cmd_tok in _WRAPPER_COMMANDS:
        inner = _strip_wrapper_flags(cmd_tok, rest)
        # Safety: if flag-stripping leaves us with an empty list or a token that
        # doesn't look like a command (e.g. a numeric value left over from a
        # missing flag entry), escalate to DESTRUCTIVE rather than silently
        # downgrading. This prevents "_strip_wrapper_flags didn't know about
        # this flag" from becoming a permission bypass.
        if not inner:
            return RawTier.MUTATING
        head = _basename(inner[0])
        if not _looks_like_command_name(head):
            return RawTier.DESTRUCTIVE
        return _segment_tier(inner)

    # eval evaluates a shell string -- conservative DESTRUCTIVE.
    if cmd_tok == "eval":
        return RawTier.DESTRUCTIVE

    # env [VAR=value ...] CMD args -- skip env assignments and recurse.
    if cmd_tok == "env":
        idx = 0
        while idx < len(rest) and _looks_like_env_assignment(rest[idx]):
            idx += 1
        return _segment_tier(rest[idx:])

    # Shell runners with -c PAYLOAD -- recursively classify the payload as a shell command.
    if cmd_tok in _SHELL_RUNNERS:
        for i, tok in enumerate(rest):
            if tok == "-c" and i + 1 < len(rest):
                inner = classify_command(rest[i + 1])
                return RawTier.DESTRUCTIVE if inner == RawTier.DESTRUCTIVE else RawTier.MUTATING
        # Shell launched without -c -- it'd hang on stdin DEVNULL anyway. MUTATING.
        return RawTier.MUTATING

    # Language runners with -c / -e -- arbitrary code, DESTRUCTIVE.
    if cmd_tok in _LANG_RUNNERS:
        if any(t in ("-c", "-e") for t in rest):
            return RawTier.DESTRUCTIVE
        return RawTier.MUTATING

    # find -delete / -exec rm ... -- destructive forms of find.
    if cmd_tok == "find":
        if "-delete" in rest:
            return RawTier.DESTRUCTIVE
        for i, tok in enumerate(rest):
            if tok == "-exec" and i + 1 < len(rest):
                if _basename(rest[i + 1]) in DESTRUCTIVE_COMMANDS:
                    return RawTier.DESTRUCTIVE
        return RawTier.MUTATING

    # tee: writes to a real file -> DESTRUCTIVE; else MUTATING.
    if cmd_tok == "tee":
        target = next((a for a in rest if not a.startswith("-")), None)
        if target is not None and target != "/dev/null":
            return RawTier.DESTRUCTIVE
        return RawTier.MUTATING

    # Default: delegate to argv-mode classifier. Shell-never-SAFE downgrade applies.
    try:
        raw = classify_argv([segment[cmd_idx], *rest])
    except BadArgvError:
        return RawTier.MUTATING
    if raw == RawTier.SAFE:
        return RawTier.MUTATING
    return raw


def _pad_shell_metachars(s: str) -> str:
    """Insert spaces around shell metacharacters (|, ;, &, >, <) outside quotes
    and backslash-escapes, so a subsequent shlex.split yields each operator as
    its own token. Multi-char operators (&&, ||, >>, 2>, &>, &>>, 1>>, 2>>) are
    kept intact."""
    out: list[str] = []
    i = 0
    n = len(s)
    in_squote = False
    in_dquote = False
    while i < n:
        c = s[i]
        if in_squote:
            out.append(c)
            if c == "'":
                in_squote = False
            i += 1
            continue
        if in_dquote:
            if c == "\\" and i + 1 < n:
                out.append(c)
                out.append(s[i + 1])
                i += 2
                continue
            out.append(c)
            if c == '"':
                in_dquote = False
            i += 1
            continue
        # Backslash escape outside quotes: pass next char through verbatim
        # so that e.g. `find ... \;` does not get its semicolon padded.
        if c == "\\" and i + 1 < n:
            out.append(c)
            out.append(s[i + 1])
            i += 2
            continue
        if c == "'":
            in_squote = True
            out.append(c)
            i += 1
            continue
        if c == '"':
            in_dquote = True
            out.append(c)
            i += 1
            continue
        three = s[i:i + 3]
        if three in ("&>>", "1>>", "2>>"):
            out.append(" ")
            out.append(three)
            out.append(" ")
            i += 3
            continue
        two = s[i:i + 2]
        if two in ("&&", "||", ">>", ">|", "<>", "&>", "1>", "2>"):
            out.append(" ")
            out.append(two)
            out.append(" ")
            i += 2
            continue
        if c in "|;&<>":
            out.append(" ")
            out.append(c)
            out.append(" ")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def classify_command(command: str) -> RawTier:
    if not command or not command.strip():
        raise BadArgvError("empty command")
    try:
        tokens = shlex.split(_pad_shell_metachars(command), posix=True)
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
