from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class RecallEntry:
    filename: str            # caller-supplied (e.g. "CVSept2025.pdf")
    summary: str
    key_points: list[str]
    last_read_iso: str       # ISO-8601 UTC
    source_mtime: float      # mtime of the underlying file at index time
    source_path: str | None  # absolute path resolved at index time, if known


class RecallIndex:
    """Filesystem-only side index for Kona's doc notes.

    Stored at <shared-root>/.kona-index/<safe-filename>.json. Keyed by the
    caller-supplied filename (typically the relative path Kona used to read
    the file), normalized to a safe basename for on-disk storage.

    Notes:
      - This is a *Kona's-notes* store, not a content cache. She writes her
        own summary + key points via index_doc; the index never auto-fills.
      - source_mtime is recorded so recall_doc can flag "your notes are
        from an older version of this file."
    """

    DIR_NAME = ".kona-index"

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.dir = self.root / self.DIR_NAME

    # ---------------------------------------------------------------- helpers

    def ensure_dir(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_name(filename: str) -> str:
        # Strip leading slashes and folder prefixes for the on-disk key, but
        # keep enough so collisions between identically-named files in
        # originals/ vs kona-edits/ are avoided.
        stem = filename.strip().lstrip("/")
        # Replace path separators with __ so the key is a single basename.
        stem = stem.replace("/", "__").replace("\\", "__")
        # Collapse anything that's not a safe filename char.
        stem = _SAFE_NAME_RE.sub("_", stem)
        return stem[:200] or "_unnamed_"

    def _path_for(self, filename: str) -> Path:
        return self.dir / f"{self._safe_name(filename)}.json"

    # ----------------------------------------------------------------- write

    def write(
        self,
        *,
        filename: str,
        summary: str,
        key_points: list[str] | str,
        source_path: Path | None = None,
    ) -> RecallEntry:
        """Persist Kona's notes about `filename`. Idempotent — overwrites
        any existing entry. Returns the stored record.
        """
        self.ensure_dir()
        # Accept key_points as either a list or a newline/bullet-separated
        # string. Empty entries are dropped.
        if isinstance(key_points, str):
            parts = re.split(r"\n+|^[-*]\s*", key_points, flags=re.MULTILINE)
            kp = [p.strip(" -*\t") for p in parts if p and p.strip(" -*\t")]
        else:
            kp = [str(p).strip() for p in key_points if str(p).strip()]

        mtime = source_path.stat().st_mtime if source_path and source_path.exists() else 0.0
        entry = RecallEntry(
            filename=filename,
            summary=summary.strip(),
            key_points=kp,
            last_read_iso=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            source_mtime=mtime,
            source_path=str(source_path) if source_path else None,
        )
        self._path_for(filename).write_text(
            json.dumps(asdict(entry), indent=2), encoding="utf-8"
        )
        return entry

    # ------------------------------------------------------------------ read

    def get(self, filename: str) -> RecallEntry | None:
        p = self._path_for(filename)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return RecallEntry(
            filename=data.get("filename", filename),
            summary=data.get("summary", ""),
            key_points=list(data.get("key_points") or []),
            last_read_iso=data.get("last_read_iso", ""),
            source_mtime=float(data.get("source_mtime") or 0.0),
            source_path=data.get("source_path"),
        )

    def list_all(self) -> list[RecallEntry]:
        if not self.dir.exists():
            return []
        out: list[RecallEntry] = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.append(RecallEntry(
                filename=data.get("filename", p.stem),
                summary=data.get("summary", ""),
                key_points=list(data.get("key_points") or []),
                last_read_iso=data.get("last_read_iso", ""),
                source_mtime=float(data.get("source_mtime") or 0.0),
                source_path=data.get("source_path"),
            ))
        return out

    # ---------------------------------------------------------- drift detect

    @staticmethod
    def is_stale(entry: RecallEntry, current_mtime: float) -> bool:
        """True when the underlying file has been modified since the notes
        were written. Tolerates sub-second clock drift.
        """
        if entry.source_mtime <= 0 or current_mtime <= 0:
            return False
        return current_mtime - entry.source_mtime > 1.0
