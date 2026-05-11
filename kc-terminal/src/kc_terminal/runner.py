from __future__ import annotations
import subprocess
import time
from pathlib import Path


def _head_tail(text: str, cap_bytes: int) -> tuple[str, bool]:
    """Return (possibly-truncated, was_truncated). Keeps head + tail with a marker."""
    encoded = text.encode("utf-8")
    if len(encoded) <= cap_bytes:
        return text, False
    half = cap_bytes // 2
    head = encoded[:half].decode("utf-8", errors="replace")
    tail = encoded[-half:].decode("utf-8", errors="replace")
    dropped = len(encoded) - 2 * half
    marker = f"\n\n...[TRUNCATED {dropped} bytes]...\n\n"
    return head + marker + tail, True


def _decode(b: bytes | None) -> str:
    """Decode child stdout/stderr bytes with replacement on invalid UTF-8.
    Subprocess output can include arbitrary bytes (cat /bin/ls, head /dev/urandom,
    etc.) so we never raise UnicodeDecodeError back to the caller."""
    if not b:
        return ""
    return b.decode("utf-8", errors="replace")


def run(
    *,
    argv: list[str] | None,
    command: str | None,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
    output_cap_bytes: int,
) -> dict:
    mode = "argv" if argv is not None else "command"
    start_ns = time.time_ns()
    try:
        if argv is not None:
            completed = subprocess.run(
                argv,
                shell=False,
                capture_output=True,
                text=False,
                cwd=str(cwd),
                env=env,
                timeout=timeout_seconds,
                stdin=subprocess.DEVNULL,
            )
        else:
            completed = subprocess.run(
                command,
                shell=True,
                executable="/bin/bash",
                capture_output=True,
                text=False,
                cwd=str(cwd),
                env=env,
                timeout=timeout_seconds,
                stdin=subprocess.DEVNULL,
            )
    except FileNotFoundError as e:
        # Disambiguate: missing cwd vs missing argv[0].
        if e.filename == str(cwd):
            return {"error": "cwd_does_not_exist", "cwd": str(cwd), "detail": str(e)}
        argv0 = argv[0] if argv else (command or "").split()[0] if command else ""
        return {"error": "executable_not_found", "argv0": argv0, "detail": str(e)}
    except PermissionError as e:
        argv0 = argv[0] if argv else (command or "").split()[0] if command else ""
        return {"error": "permission_denied", "argv0": argv0, "detail": str(e)}
    except subprocess.TimeoutExpired as e:
        duration_ms = (time.time_ns() - start_ns) // 1_000_000
        out, out_tr = _head_tail(_decode(e.stdout), output_cap_bytes)
        err, err_tr = _head_tail(_decode(e.stderr), output_cap_bytes)
        return {
            "mode": mode,
            "exit_code": -1,
            "stdout": out,
            "stdout_truncated": out_tr,
            "stderr": err,
            "stderr_truncated": err_tr,
            "duration_ms": duration_ms,
            "timed_out": True,
        }
    duration_ms = (time.time_ns() - start_ns) // 1_000_000
    stdout, stdout_tr = _head_tail(_decode(completed.stdout), output_cap_bytes)
    stderr, stderr_tr = _head_tail(_decode(completed.stderr), output_cap_bytes)
    return {
        "mode": mode,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stdout_truncated": stdout_tr,
        "stderr": stderr,
        "stderr_truncated": stderr_tr,
        "duration_ms": duration_ms,
        "timed_out": False,
    }
