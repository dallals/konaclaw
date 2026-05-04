from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal
import yaml


class ShareError(Exception):
    pass


Mode = Literal["read-write", "read-only"]


@dataclass
class Share:
    name: str
    path: Path
    mode: Mode = "read-write"

    def __post_init__(self) -> None:
        self.path = Path(self.path).expanduser().resolve()
        if not self.path.is_dir():
            raise ShareError(f"share {self.name!r}: path {self.path} is not a directory")
        if self.mode not in ("read-write", "read-only"):
            raise ShareError(f"share {self.name!r}: mode must be read-write or read-only")


class SharesRegistry:
    def __init__(self, shares: Iterable[Share]) -> None:
        self._by_name: dict[str, Share] = {}
        for s in shares:
            if s.name in self._by_name:
                raise ShareError(f"duplicate share: {s.name}")
            self._by_name[s.name] = s

    @classmethod
    def from_yaml(cls, path: Path | str) -> "SharesRegistry":
        data = yaml.safe_load(Path(path).read_text()) or {}
        shares = [
            Share(name=s["name"], path=Path(s["path"]), mode=s.get("mode", "read-write"))
            for s in data.get("shares", [])
        ]
        return cls(shares)

    def names(self) -> list[str]:
        return list(self._by_name.keys())

    def get(self, name: str) -> Share:
        if name not in self._by_name:
            raise ShareError(f"unknown share: {name}")
        return self._by_name[name]

    def can_read(self, name: str) -> bool:
        return name in self._by_name

    def can_write(self, name: str) -> bool:
        return self.get(name).mode == "read-write"

    def resolve(self, name: str, relpath: str) -> Path:
        share = self.get(name)
        rp = Path(relpath)
        if rp.is_absolute():
            raise ShareError(f"share {name!r}: relpath must be relative, got {relpath!r}")

        # Build the candidate path then fully resolve symlinks
        candidate = (share.path / rp).resolve()

        # The fully-resolved candidate must be inside the share root.
        try:
            candidate.relative_to(share.path)
        except ValueError:
            raise ShareError(f"share {name!r}: path {relpath!r} escapes share root")
        return candidate
