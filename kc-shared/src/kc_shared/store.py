from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class SharedFileError(Exception):
    """Base class for shared-folder errors."""


class SharedFileNotFound(SharedFileError):
    pass


class SharedPathOutOfScope(SharedFileError):
    """Raised when a path resolves outside the allowed subtree (traversal block)."""


@dataclass
class SharedFileInfo:
    relpath: str
    folder: str
    size_bytes: int
    modified_at: float


_CONV_FOLDER_RE = re.compile(r"-conv(\d+)$")
_FILENAME_RE = re.compile(r"^[A-Za-z0-9._\- ]+$")


class SharedStore:
    """Filesystem-only store over a single root.

    Layout:
        <root>/originals/             — read-only side (user puts files here)
        <root>/kona-edits/<sub>/      — writable, one folder per conversation
                                        (named "YYYY-MM-DD-HHMM-conv<id>")

    Both subtrees are auto-created on first access. Per-conversation edit
    folders are created lazily on the first write_edit call.
    """

    ORIGINALS = "originals"
    EDITS = "kona-edits"

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()

    # ------------------------------------------------------------------ paths

    def ensure_dirs(self) -> None:
        (self.root / self.ORIGINALS).mkdir(parents=True, exist_ok=True)
        (self.root / self.EDITS).mkdir(parents=True, exist_ok=True)

    def originals_dir(self) -> Path:
        d = self.root / self.ORIGINALS
        d.mkdir(parents=True, exist_ok=True)
        return d

    def edits_root(self) -> Path:
        d = self.root / self.EDITS
        d.mkdir(parents=True, exist_ok=True)
        return d

    def edits_dir_for(self, conversation_id: str, *, create: bool = False) -> Path:
        """Return the per-conversation edits folder.

        Looks for any existing folder matching `*-conv<id>` first so writes
        in the same chat keep landing in the same folder across supervisor
        restarts. When none exists and `create=True`, creates a fresh folder
        named "YYYY-MM-DD-HHMM-conv<id>".
        """
        edits = self.edits_root()
        suffix = f"-conv{conversation_id}"
        for entry in edits.iterdir() if edits.exists() else []:
            if entry.is_dir() and entry.name.endswith(suffix):
                return entry
        if not create:
            return edits / f"<unmaterialized-conv{conversation_id}>"
        ts = datetime.now().strftime("%Y-%m-%d-%H%M")
        new_dir = edits / f"{ts}-conv{conversation_id}"
        new_dir.mkdir(parents=True, exist_ok=False)
        return new_dir

    # ----------------------------------------------------------------- listing

    def list_originals(self) -> list[SharedFileInfo]:
        return self._list_under(self.originals_dir(), folder=self.ORIGINALS)

    def list_edits(self, conversation_id: str) -> list[SharedFileInfo]:
        folder = self.edits_dir_for(conversation_id, create=False)
        if not folder.exists() or not folder.is_dir():
            return []
        return self._list_under(folder, folder=self.EDITS)

    def _list_under(self, root: Path, *, folder: str) -> list[SharedFileInfo]:
        out: list[SharedFileInfo] = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            stat = p.stat()
            out.append(SharedFileInfo(
                relpath=str(p.relative_to(root)),
                folder=folder,
                size_bytes=stat.st_size,
                modified_at=stat.st_mtime,
            ))
        return out

    # ------------------------------------------------------------------ read

    def read_file(self, relpath: str, *, conversation_id: str | None = None) -> tuple[bytes, Path]:
        """Resolve `relpath` under the shared root and return (bytes, abspath).

        Accepted forms:
            "originals/foo.pdf"
            "kona-edits/<any-subfolder>/notes.md"
            "foo.pdf"           — treated as originals/foo.pdf

        Conversation-scoped: when conversation_id is given and the path is
        under kona-edits/, only the caller's own edits folder is readable.
        """
        candidate = self._resolve_read_path(relpath, conversation_id=conversation_id)
        if not candidate.exists() or not candidate.is_file():
            raise SharedFileNotFound(f"shared file not found: {relpath}")
        return candidate.read_bytes(), candidate

    def _resolve_read_path(self, relpath: str, *, conversation_id: str | None) -> Path:
        rp = relpath.strip().lstrip("/")
        if not rp:
            raise SharedPathOutOfScope("empty path")
        first, *rest = rp.split("/", 1)
        if first in (self.ORIGINALS, self.EDITS):
            base = self.root / first
            tail = rest[0] if rest else ""
        else:
            base = self.originals_dir()
            tail = rp
        target = (base / tail).resolve() if tail else base.resolve()
        # Path-traversal guard: target must be within the shared root.
        try:
            target.relative_to(self.root)
        except ValueError as e:
            raise SharedPathOutOfScope(f"path escapes shared root: {relpath}") from e
        # Edits scope: when a conversation id is supplied and the path lands
        # under kona-edits, require it sits in that conversation's folder.
        # If the caller has no folder yet, nothing under kona-edits is theirs.
        if conversation_id is not None and self._is_under(target, self.edits_root()):
            own = self.edits_dir_for(conversation_id, create=False)
            if not own.exists() or not self._is_under(target, own):
                raise SharedPathOutOfScope(
                    f"path is in another conversation's edits folder: {relpath}"
                )
        return target

    @staticmethod
    def _is_under(target: Path, ancestor: Path) -> bool:
        try:
            target.resolve().relative_to(ancestor.resolve())
            return True
        except (ValueError, FileNotFoundError):
            return False

    # ----------------------------------------------------------------- write

    def write_edit(
        self,
        *,
        conversation_id: str,
        filename: str,
        content: bytes,
    ) -> Path:
        """Write `content` to <edits>/<conv-folder>/<filename>. Creates folder lazily.

        Filename must be a plain basename (no slashes, no traversal). The
        whitelist regex limits to letters/digits/dot/underscore/hyphen/space.
        """
        if not filename:
            raise SharedPathOutOfScope("empty filename")
        if "/" in filename or "\\" in filename or filename in (".", ".."):
            raise SharedPathOutOfScope(f"filename must be a basename: {filename!r}")
        if not _FILENAME_RE.match(filename):
            raise SharedPathOutOfScope(
                f"filename has disallowed characters: {filename!r}"
            )
        folder = self.edits_dir_for(conversation_id, create=True)
        target = folder / filename
        # Defensive double-check after resolve, in case of symlink shenanigans.
        if not self._is_under(target, folder):
            raise SharedPathOutOfScope(f"resolved path escapes edits folder: {filename}")
        target.write_bytes(content)
        return target
