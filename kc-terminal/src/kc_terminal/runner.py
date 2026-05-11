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
                text=True,
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
                text=True,
                cwd=str(cwd),
                env=env,
                timeout=timeout_seconds,
                stdin=subprocess.DEVNULL,
            )
    except FileNotFoundError as e:
        return {
            "error": "executable_not_found",
            "argv0": (argv[0] if argv else (command or "").split()[0] if command else ""),
            "detail": str(e),
        }
    except subprocess.TimeoutExpired as e:
        duration_ms = (time.time_ns() - start_ns) // 1_000_000
        stdout_text = (e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")) or ""
        stderr_text = (e.stderr if isinstance(e.stderr, str) else (e.stderr or b"").decode("utf-8", "replace")) or ""
        out, out_tr = _head_tail(stdout_text, output_cap_bytes)
        err, err_tr = _head_tail(stderr_text, output_cap_bytes)
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
    stdout, stdout_tr = _head_tail(completed.stdout or "", output_cap_bytes)
    stderr, stderr_tr = _head_tail(completed.stderr or "", output_cap_bytes)
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
