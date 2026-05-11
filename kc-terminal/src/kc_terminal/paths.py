from __future__ import annotations
from pathlib import Path


class CwdNotAbsolute(ValueError):
    pass


class CwdDoesNotExist(ValueError):
    pass


class CwdNotADirectory(ValueError):
    pass


class CwdOutsideRoots(ValueError):
    pass


def validate_cwd(cwd_str: str, roots: list[Path]) -> Path:
    p = Path(cwd_str)
    if not p.is_absolute():
        raise CwdNotAbsolute(cwd_str)
    try:
        p_resolved = p.resolve(strict=True)
    except FileNotFoundError as e:
        raise CwdDoesNotExist(cwd_str) from e
    if not p_resolved.is_dir():
        raise CwdNotADirectory(str(p_resolved))
    for root in roots:
        try:
            root_resolved = root.resolve(strict=True)
        except FileNotFoundError:
            continue  # missing root is just skipped
        if p_resolved == root_resolved or root_resolved in p_resolved.parents:
            return p_resolved
    raise CwdOutsideRoots(str(p_resolved))
