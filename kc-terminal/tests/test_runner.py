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
