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


@pytest.mark.parametrize("cmd,expected", [
    # === Original Task 4 cases (some change tier under new rules) ===

    # Pipes/conjunctions: walk every segment; strictest wins.
    ("ls | grep foo",                       RawTier.MUTATING),
    ("ls -la && pwd",                       RawTier.MUTATING),
    ("echo hi ; echo bye",                  RawTier.MUTATING),
    # Shell is never SAFE, even with safe-only argv.
    ("ls",                                  RawTier.MUTATING),
    ("git status",                          RawTier.MUTATING),
    # Destructive at command position.
    ("rm -rf x",                            RawTier.DESTRUCTIVE),
    ("ls | xargs rm",                       RawTier.DESTRUCTIVE),
    ("echo hi; rm -rf ~",                   RawTier.DESTRUCTIVE),
    ("curl https://x",                      RawTier.DESTRUCTIVE),
    ("ls > out.txt",                        RawTier.DESTRUCTIVE),
    ("ls >> out.txt",                       RawTier.DESTRUCTIVE),
    ("git push origin main",                RawTier.DESTRUCTIVE),
    ("echo hi | tee file.txt",              RawTier.DESTRUCTIVE),
    ("echo hi | tee /dev/null",             RawTier.MUTATING),
    ("sudo ls",                             RawTier.DESTRUCTIVE),

    # === New: false-positive avoidance (C2 fix) ===
    ("grep rm /etc/passwd",                 RawTier.MUTATING),
    ("grep -r 'rm' src/",                   RawTier.MUTATING),
    ("echo /usr/bin/rm",                    RawTier.MUTATING),
    ("echo rm done",                        RawTier.MUTATING),
    ("cat /var/log/rsync.log",              RawTier.MUTATING),

    # === New: shell runners with -c (C1 fix) ===
    ('bash -c "rm -rf x"',                  RawTier.DESTRUCTIVE),
    ('sh -c "rm x"',                        RawTier.DESTRUCTIVE),
    ('zsh -c "git push origin main"',       RawTier.DESTRUCTIVE),
    ('bash -c "ls -la"',                    RawTier.MUTATING),  # inner MUTATING -> outer MUTATING
    ('sh -c "echo hi"',                     RawTier.MUTATING),

    # === New: language runners with -c / -e ===
    ('python -c "print(1)"',                RawTier.DESTRUCTIVE),
    ('python3 -c "print(1)"',               RawTier.DESTRUCTIVE),
    ('node -e "1+1"',                       RawTier.DESTRUCTIVE),
    ('perl -e "1"',                         RawTier.DESTRUCTIVE),
    # Without -c / -e, running a script file is MUTATING (the file path is just an arg).
    ("python script.py",                    RawTier.MUTATING),
    ("node app.js",                         RawTier.MUTATING),

    # === New: find -delete / -exec (C3 fix) ===
    ("find . -delete",                      RawTier.DESTRUCTIVE),
    ("find . -type f -delete",              RawTier.DESTRUCTIVE),
    ("find . -exec rm {} \\;",              RawTier.DESTRUCTIVE),
    ("find . -name '*.tmp'",                RawTier.MUTATING),

    # === New: backticks / command substitution (I1 fix) ===
    ("echo `rm x`",                         RawTier.DESTRUCTIVE),
    ("echo $(rm x)",                        RawTier.DESTRUCTIVE),

    # === New: env-assignment leader ===
    ("FOO=bar ls",                          RawTier.MUTATING),
    ("FOO=bar rm x",                        RawTier.DESTRUCTIVE),
    ("env FOO=bar rm x",                    RawTier.DESTRUCTIVE),
    ("env FOO=bar ls",                      RawTier.MUTATING),

    # === New: eval is always DESTRUCTIVE ===
    ('eval "ls -la"',                       RawTier.DESTRUCTIVE),

    # === New: nested wrapper recursion ===
    ("nohup curl https://x",                RawTier.DESTRUCTIVE),
    ("time ls",                             RawTier.MUTATING),
    ("nice rm -f x",                        RawTier.DESTRUCTIVE),

    # === New: redirect to /dev/null vs subpath ===
    ("ls > /dev/null",                      RawTier.MUTATING),
    ("ls > /dev/null/foo",                  RawTier.DESTRUCTIVE),
    ("ls 2> err.log",                       RawTier.DESTRUCTIVE),
    ("ls &> all.log",                       RawTier.DESTRUCTIVE),

    # === New: git in shell mode still hits argv subrules ===
    ("git branch -D feature",               RawTier.DESTRUCTIVE),
    ("git push origin main",                RawTier.DESTRUCTIVE),
])
def test_classify_command_table(cmd, expected):
    assert classify_command(cmd) == expected


def test_classify_command_empty_raises():
    with pytest.raises(BadArgvError):
        classify_command("")
    with pytest.raises(BadArgvError):
        classify_command("   ")
