import pytest
from kc_terminal.classifier import (
    classify_argv,
    classify_command,
    RawTier,
    BadArgvError,
)


@pytest.mark.parametrize("argv,expected", [
    (["ls"], RawTier.SAFE),
    (["ls", "-la"], RawTier.SAFE),
    (["cat", "/etc/hosts"], RawTier.SAFE),
    (["grep", "foo", "bar.txt"], RawTier.SAFE),
    (["pwd"], RawTier.SAFE),
    (["echo", "hi"], RawTier.SAFE),
    (["env"], RawTier.SAFE),
])
def test_argv_safe_commands(argv, expected):
    assert classify_argv(argv) == expected


@pytest.mark.parametrize("argv,expected", [
    (["rm", "-rf", "x"], RawTier.DESTRUCTIVE),
    (["sudo", "ls"], RawTier.DESTRUCTIVE),
    (["curl", "https://x"], RawTier.DESTRUCTIVE),
    (["wget", "https://x"], RawTier.DESTRUCTIVE),
    (["ssh", "host"], RawTier.DESTRUCTIVE),
    (["chmod", "+x", "f"], RawTier.DESTRUCTIVE),
    (["mv", "a", "b"], RawTier.DESTRUCTIVE),
    (["cp", "a", "b"], RawTier.DESTRUCTIVE),
])
def test_argv_destructive_commands(argv, expected):
    assert classify_argv(argv) == expected


@pytest.mark.parametrize("argv,expected", [
    (["python", "-c", "print(1)"], RawTier.MUTATING),
    (["python3", "-c", "print(1)"], RawTier.MUTATING),
    (["node", "-e", "1"], RawTier.MUTATING),
    (["npm", "install"], RawTier.MUTATING),
    (["pip", "install", "x"], RawTier.MUTATING),
    (["pytest"], RawTier.MUTATING),
    (["make", "build"], RawTier.MUTATING),
    (["docker", "ps"], RawTier.MUTATING),
    (["some-unknown-tool"], RawTier.MUTATING),
])
def test_argv_default_mutating(argv, expected):
    assert classify_argv(argv) == expected


def test_argv_normalizes_basename():
    assert classify_argv(["/usr/bin/ls"]) == RawTier.SAFE
    assert classify_argv(["/usr/local/bin/rm", "x"]) == RawTier.DESTRUCTIVE


def test_argv_empty_raises():
    with pytest.raises(BadArgvError):
        classify_argv([])


@pytest.mark.parametrize("argv,expected", [
    (["git", "status"], RawTier.SAFE),
    (["git", "log", "--oneline"], RawTier.SAFE),
    (["git", "diff"], RawTier.SAFE),
    (["git", "show", "HEAD"], RawTier.SAFE),
    (["git", "blame", "f"], RawTier.SAFE),
    (["git", "branch"], RawTier.SAFE),
    (["git", "remote", "-v"], RawTier.SAFE),
    (["git", "rev-parse", "HEAD"], RawTier.SAFE),
    (["git", "describe"], RawTier.SAFE),
    (["git", "fetch"], RawTier.SAFE),
    (["git", "ls-files"], RawTier.SAFE),
])
def test_argv_git_safe_subcommands(argv, expected):
    assert classify_argv(argv) == expected


@pytest.mark.parametrize("argv,expected", [
    (["git", "push"], RawTier.DESTRUCTIVE),
    (["git", "push", "origin", "main"], RawTier.DESTRUCTIVE),
    (["git", "filter-repo", "--invert-paths"], RawTier.DESTRUCTIVE),
    (["git", "reset", "--hard", "HEAD"], RawTier.DESTRUCTIVE),
    (["git", "clean", "-fd"], RawTier.DESTRUCTIVE),
    (["git", "branch", "-D", "x"], RawTier.DESTRUCTIVE),
    (["git", "tag", "-d", "v1"], RawTier.DESTRUCTIVE),
    (["git", "clean", "-f"], RawTier.DESTRUCTIVE),
    (["git", "clean", "-fx"], RawTier.DESTRUCTIVE),
    (["git", "clean", "--force"], RawTier.DESTRUCTIVE),
    (["git", "tag", "--delete", "v1"], RawTier.DESTRUCTIVE),
])
def test_argv_git_destructive_subcommands(argv, expected):
    assert classify_argv(argv) == expected


@pytest.mark.parametrize("argv,expected", [
    (["git", "commit", "-m", "x"], RawTier.MUTATING),
    (["git", "checkout", "main"], RawTier.MUTATING),
    (["git", "merge", "x"], RawTier.MUTATING),
    (["git", "pull"], RawTier.MUTATING),
    (["git", "add", "."], RawTier.MUTATING),
    (["git", "stash"], RawTier.MUTATING),
    (["git", "clean", "-n"], RawTier.MUTATING),
    (["git", "clean", "-d"], RawTier.MUTATING),
])
def test_argv_git_default_mutating(argv, expected):
    assert classify_argv(argv) == expected


@pytest.mark.parametrize("argv,expected", [
    (["gh", "repo", "view"], RawTier.SAFE),
    (["gh", "repo", "list"], RawTier.SAFE),
    (["gh", "pr", "view", "123"], RawTier.SAFE),
    (["gh", "pr", "list"], RawTier.SAFE),
    (["gh", "pr", "status"], RawTier.SAFE),
    (["gh", "pr", "diff", "123"], RawTier.SAFE),
    (["gh", "issue", "view", "1"], RawTier.SAFE),
    (["gh", "issue", "list"], RawTier.SAFE),
    (["gh", "issue", "status"], RawTier.SAFE),
    (["gh", "run", "view", "42"], RawTier.SAFE),
    (["gh", "run", "list"], RawTier.SAFE),
])
def test_argv_gh_safe_pairs(argv, expected):
    assert classify_argv(argv) == expected


@pytest.mark.parametrize("argv,expected", [
    (["gh", "pr", "merge", "123"], RawTier.DESTRUCTIVE),
    (["gh", "pr", "close", "123"], RawTier.DESTRUCTIVE),
    (["gh", "repo", "delete", "x"], RawTier.DESTRUCTIVE),
    (["gh", "secret", "delete", "FOO"], RawTier.DESTRUCTIVE),
    (["gh", "release", "delete", "v1"], RawTier.DESTRUCTIVE),
])
def test_argv_gh_destructive_pairs(argv, expected):
    assert classify_argv(argv) == expected


@pytest.mark.parametrize("argv,expected", [
    (["gh", "pr", "create"], RawTier.MUTATING),
    (["gh", "issue", "create"], RawTier.MUTATING),
    (["gh", "api", "/repos/x/y"], RawTier.MUTATING),
    (["gh", "auth", "status"], RawTier.MUTATING),
])
def test_argv_gh_default_mutating(argv, expected):
    assert classify_argv(argv) == expected


def test_argv_normalizes_relative_paths():
    """Relative paths should still basename-normalize correctly."""
    assert classify_argv(["./rm", "x"]) == RawTier.DESTRUCTIVE
    assert classify_argv(["bin/ls"]) == RawTier.SAFE


def test_safe_and_destructive_sets_disjoint():
    """Invariant: a command must not appear in both sets — keeps the
    DESTRUCTIVE>SAFE>MUTATING priority order from masking accidental overlap."""
    from kc_terminal.classifier import SAFE_COMMANDS, DESTRUCTIVE_COMMANDS
    assert SAFE_COMMANDS.isdisjoint(DESTRUCTIVE_COMMANDS)
