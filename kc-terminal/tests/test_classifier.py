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
