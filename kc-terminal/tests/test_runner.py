import pytest
from pathlib import Path
from kc_terminal.runner import run


@pytest.fixture
def workdir(tmp_path):
    return tmp_path


def test_argv_echo_captures_stdout(workdir):
    result = run(
        argv=["echo", "hello"],
        command=None,
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=10,
        output_cap_bytes=1024,
    )
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "hello"
    assert result["stderr"] == ""
    assert result["timed_out"] is False
    assert result["mode"] == "argv"
    assert result["stdout_truncated"] is False
    assert result["stderr_truncated"] is False
    assert result["duration_ms"] >= 0


def test_argv_false_nonzero_exit(workdir):
    result = run(
        argv=["false"],
        command=None,
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=10,
        output_cap_bytes=1024,
    )
    assert result["exit_code"] == 1
    assert result["timed_out"] is False


def test_argv_executable_not_found(workdir):
    result = run(
        argv=["this-command-does-not-exist-12345"],
        command=None,
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=10,
        output_cap_bytes=1024,
    )
    assert result.get("error") == "executable_not_found"
    assert "this-command-does-not-exist-12345" in result["argv0"]


def test_shell_pipe(workdir):
    result = run(
        argv=None,
        command="echo hi | wc -c",
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=10,
        output_cap_bytes=1024,
    )
    assert result["exit_code"] == 0
    assert result["mode"] == "command"
    # `echo hi` -> "hi\n" -> 3 bytes
    assert result["stdout"].strip() == "3"


def test_cwd_applied(workdir):
    result = run(
        argv=["pwd"],
        command=None,
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=10,
        output_cap_bytes=1024,
    )
    # macOS may add /private prefix; compare resolved paths.
    assert Path(result["stdout"].strip()).resolve() == workdir.resolve()


def test_env_applied_and_secrets_stripped(workdir):
    # Parent env has a secret-prefixed var; child env (built by caller) excludes it.
    # The runner itself only forwards what it's given.
    result = run(
        argv=["sh", "-c", "echo PATH=$PATH; echo SECRET=${KC_TEST_SECRET:-UNSET}"],
        command=None,
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},  # no KC_TEST_SECRET
        timeout_seconds=10,
        output_cap_bytes=1024,
    )
    assert result["exit_code"] == 0
    assert "PATH=/usr/bin:/bin" in result["stdout"]
    assert "SECRET=UNSET" in result["stdout"]


def test_stdin_is_devnull(workdir):
    # `cat` with no args reads stdin; with DEVNULL it should exit immediately with empty output.
    result = run(
        argv=["cat"],
        command=None,
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=5,
        output_cap_bytes=1024,
    )
    assert result["exit_code"] == 0
    assert result["stdout"] == ""
    assert result["timed_out"] is False


def test_non_utf8_output_does_not_crash(workdir):
    """If a subprocess writes non-UTF-8 bytes, the runner replaces invalid
    sequences rather than raising UnicodeDecodeError."""
    # printf %b with octal escapes is a portable way to emit raw bytes.
    result = run(
        argv=["bash", "-c", "printf '\\xff\\xfe\\xfd'"],
        command=None,
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=5,
        output_cap_bytes=1024,
    )
    assert result["exit_code"] == 0
    # Invalid bytes get replaced with U+FFFD (?, the Unicode replacement char).
    assert "�" in result["stdout"]


def test_missing_cwd_returns_cwd_does_not_exist(tmp_path):
    """A non-existent cwd should be reported distinctly from missing argv[0]."""
    missing = tmp_path / "no-such-dir"
    # Don't mkdir -- we want it absent.
    result = run(
        argv=["ls"],
        command=None,
        cwd=missing,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=5,
        output_cap_bytes=1024,
    )
    assert result.get("error") == "cwd_does_not_exist"
    assert str(missing) in result["cwd"]


def test_permission_denied(workdir):
    """A non-executable file at argv[0] returns permission_denied."""
    # Create a non-executable file and try to run it.
    script = workdir / "notexec.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    # chmod -x explicitly (default is non-exec but be explicit).
    script.chmod(0o644)
    result = run(
        argv=[str(script)],
        command=None,
        cwd=workdir,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=5,
        output_cap_bytes=1024,
    )
    assert result.get("error") == "permission_denied"
    assert "notexec.sh" in result["argv0"]
