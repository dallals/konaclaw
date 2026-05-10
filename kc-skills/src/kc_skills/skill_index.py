"""In-memory skill index with mtime invalidation.

Walks ~/KonaClaw/skills/ two levels deep collecting SKILL.md files. Each
read-side method (list, get, read_supporting_file, script_path) calls
`_refresh_if_changed()` which mtime-checks every known SKILL.md plus
discovers new ones. Re-parsing happens only for files whose mtime changed.

Public API:
  - SkillIndex(root: Path)
  - .list() -> list[SkillSummary]
  - .get(name) -> Optional[Skill]
  - .read_supporting_file(name, file_path) -> Optional[str]
  - .script_path(name, script_name) -> Optional[Path]

  Raises PathOutsideSkillDir on path-escape attempts.
"""
from __future__ import annotations
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from kc_skills.frontmatter import (
    FrontmatterError,
    parse_skill_frontmatter,
    skill_matches_platform,
)

logger = logging.getLogger(__name__)


_SUPPORTING_DIRS = ("references", "templates", "assets", "scripts")


class PathOutsideSkillDir(Exception):
    """Raised when a caller-supplied file_path resolves outside the skill's dir."""


@dataclass(frozen=True)
class SkillSummary:
    name: str
    category: Optional[str]
    description: str
    version: Optional[str]
    platforms: Optional[list[str]]
    tags: list[str]
    related_skills: list[str]
    skill_dir: Path


@dataclass(frozen=True)
class Skill:
    summary: SkillSummary
    body: str
    supporting_files: dict[str, list[str]]


@dataclass
class _Entry:
    mtime_ns: int
    summary: SkillSummary
    body: str


class SkillIndex:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = threading.Lock()
        self._by_name: dict[str, _Entry] = {}
        # Maps SKILL.md absolute path -> skill name, used to drop entries
        # whose file disappeared.
        self._path_to_name: dict[Path, str] = {}

    # -- public ------------------------------------------------------------

    def list(self) -> list[SkillSummary]:
        with self._lock:
            self._refresh_if_changed()
            return sorted(
                (e.summary for e in self._by_name.values()),
                key=lambda s: (s.category or "", s.name),
            )

    def get(self, name: str) -> Optional[Skill]:
        with self._lock:
            self._refresh_if_changed()
            entry = self._by_name.get(name)
            if entry is None:
                return None
            return Skill(
                summary=entry.summary,
                body=entry.body,
                supporting_files=self._scan_supporting_files(entry.summary.skill_dir),
            )

    def read_supporting_file(self, name: str, file_path: str) -> Optional[str]:
        with self._lock:
            self._refresh_if_changed()
            entry = self._by_name.get(name)
            if entry is None:
                return None
            target = self._resolve_inside_skill(entry.summary.skill_dir, file_path)
            if not target.exists() or not target.is_file():
                return None
            return target.read_text(encoding="utf-8")

    def script_path(self, name: str, script_name: str) -> Optional[Path]:
        with self._lock:
            self._refresh_if_changed()
            entry = self._by_name.get(name)
            if entry is None:
                return None
            target = self._resolve_inside_skill(entry.summary.skill_dir, f"scripts/{script_name}")
            if not target.exists() or not target.is_file():
                return None
            return target

    # -- internals ---------------------------------------------------------

    def _refresh_if_changed(self) -> None:
        """Caller must hold self._lock.

        Walks the skills root, mtime-checking each SKILL.md. Reparses only
        the changed/new ones. Drops entries whose file disappeared.
        """
        if not self._root.is_dir():
            # Drop everything if the root vanished.
            self._by_name.clear()
            self._path_to_name.clear()
            return

        seen: dict[Path, int] = {}  # path -> mtime_ns
        # Accept BOTH layouts: <root>/<category>/<skill>/SKILL.md
        # and <root>/<skill>/SKILL.md.
        try:
            top_entries = list(os.scandir(self._root))
        except FileNotFoundError:
            self._by_name.clear()
            self._path_to_name.clear()
            return

        for top in top_entries:
            if not top.is_dir():
                continue
            top_path = Path(top.path)
            # Direct flat-layout skill?
            flat_md = top_path / "SKILL.md"
            if flat_md.is_file():
                seen[flat_md] = flat_md.stat().st_mtime_ns
                continue
            # Otherwise it's a category folder -- scan its children.
            try:
                inner_entries = list(os.scandir(top_path))
            except FileNotFoundError:
                continue
            for inner in inner_entries:
                if not inner.is_dir():
                    continue
                inner_path = Path(inner.path)
                md = inner_path / "SKILL.md"
                if md.is_file():
                    seen[md] = md.stat().st_mtime_ns

        # Drop entries whose file disappeared.
        for path in list(self._path_to_name.keys()):
            if path not in seen:
                name = self._path_to_name.pop(path)
                self._by_name.pop(name, None)

        # For each seen path, decide whether to re-parse.
        # Process in deterministic order (sorted by path) so duplicate-name
        # collisions resolve identically across runs.
        for md_path in sorted(seen.keys()):
            mtime = seen[md_path]
            existing_name = self._path_to_name.get(md_path)
            if existing_name is not None:
                existing = self._by_name.get(existing_name)
                if existing is not None and existing.mtime_ns == mtime:
                    continue  # cache hit
            # Re-parse.
            self._ingest(md_path, mtime)

    def _ingest(self, md_path: Path, mtime_ns: int) -> None:
        """Caller must hold self._lock.

        Parses one SKILL.md and updates internal maps. On parse error,
        logs and drops any prior entry for this path.
        """
        # If this path previously held a different name, drop the old entry.
        prior_name = self._path_to_name.pop(md_path, None)
        if prior_name is not None:
            self._by_name.pop(prior_name, None)

        try:
            raw = md_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("could not read %s: %s", md_path, e)
            return

        try:
            fm, body = parse_skill_frontmatter(raw)
        except FrontmatterError as e:
            logger.warning("skipping %s: %s", md_path, e)
            return

        if not skill_matches_platform(fm):
            # Platform-excluded skills are not ingested at all.
            return

        skill_dir = md_path.parent
        # Two-level layout: parent of skill_dir is a category iff its parent
        # is the root. Flat: skill_dir is directly under root.
        category: Optional[str] = None
        try:
            rel = skill_dir.relative_to(self._root)
            if len(rel.parts) == 2:
                category = rel.parts[0]
        except ValueError:
            pass

        name = fm["name"]
        if name in self._by_name:
            existing_path = next(
                (p for p, n in self._path_to_name.items() if n == name),
                None,
            )
            logger.warning(
                "duplicate skill name %r at %s (already indexed at %s); skipping",
                name, md_path, existing_path,
            )
            return

        summary = SkillSummary(
            name=name,
            category=category,
            description=fm["description"],
            version=fm.get("version"),
            platforms=fm.get("platforms") if isinstance(fm.get("platforms"), list) else None,
            tags=list(fm.get("tags") or []),
            related_skills=list(fm.get("related_skills") or []),
            skill_dir=skill_dir,
        )
        self._by_name[name] = _Entry(mtime_ns=mtime_ns, summary=summary, body=body)
        self._path_to_name[md_path] = name

    @staticmethod
    def _scan_supporting_files(skill_dir: Path) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {d: [] for d in _SUPPORTING_DIRS}
        for d in _SUPPORTING_DIRS:
            sub = skill_dir / d
            if not sub.is_dir():
                continue
            for entry in sorted(sub.rglob("*")):
                if entry.is_file() and not entry.is_symlink():
                    rel = str(entry.relative_to(sub))
                    out[d].append(rel)
        return out

    @staticmethod
    def _resolve_inside_skill(skill_dir: Path, file_path: str) -> Path:
        """Resolve `file_path` against `skill_dir`, rejecting any path that
        escapes (via `..`, symlinks pointing out, or absolute paths)."""
        if Path(file_path).is_absolute():
            raise PathOutsideSkillDir(f"absolute path not allowed: {file_path}")
        candidate = (skill_dir / file_path).resolve()
        skill_root = skill_dir.resolve()
        try:
            candidate.relative_to(skill_root)
        except ValueError:
            raise PathOutsideSkillDir(
                f"path {file_path!r} escapes skill dir {skill_dir}"
            )
        return candidate
