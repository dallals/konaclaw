from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from kc_terminal.env import DEFAULT_SECRET_PREFIXES


def _default_roots() -> tuple[Path, ...]:
    home = Path.home()
    return (
        home / "KonaClaw",
        home / "Desktop" / "claudeCode" / "SammyClaw",
    )


@dataclass(frozen=True)
class TerminalConfig:
    roots: tuple[Path, ...]
    secret_prefixes: tuple[str, ...]
    default_timeout_seconds: int
    max_timeout_seconds: int
    output_cap_bytes: int

    @classmethod
    def with_defaults(cls) -> "TerminalConfig":
        return cls(
            roots=_default_roots(),
            secret_prefixes=DEFAULT_SECRET_PREFIXES,
            default_timeout_seconds=60,
            max_timeout_seconds=600,
            output_cap_bytes=128 * 1024,
        )

    @classmethod
    def from_env(cls) -> "TerminalConfig":
        base = cls.with_defaults()
        roots_raw = os.environ.get("KC_TERMINAL_ROOTS")
        roots = tuple(Path(p) for p in roots_raw.split(":") if p) if roots_raw else base.roots
        default_to = int(os.environ.get("KC_TERMINAL_DEFAULT_TIMEOUT", base.default_timeout_seconds))
        max_to = int(os.environ.get("KC_TERMINAL_MAX_TIMEOUT", base.max_timeout_seconds))
        cap = int(os.environ.get("KC_TERMINAL_OUTPUT_CAP_BYTES", base.output_cap_bytes))
        return cls(
            roots=roots,
            secret_prefixes=base.secret_prefixes,
            default_timeout_seconds=default_to,
            max_timeout_seconds=max_to,
            output_cap_bytes=cap,
        )

    def clamp_timeout(self, requested: int | None) -> int:
        if requested is None:
            return self.default_timeout_seconds
        if requested < 1:
            return 1
        if requested > self.max_timeout_seconds:
            return self.max_timeout_seconds
        return requested
