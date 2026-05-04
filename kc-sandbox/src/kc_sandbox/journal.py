from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Iterable


class JournalError(Exception):
    pass


class Journal:
    """A per-share git journal stored at <share_root>/.kc-journal/.

    No .git directory or file is created in the share root. All git
    invocations pass --git-dir and --work-tree explicitly, so the share
    root looks like a normal directory to the user.
    """

    JOURNAL_DIR_NAME = ".kc-journal"

    def __init__(self, share_root: Path) -> None:
        self.root = Path(share_root).resolve()
        self.git_dir = self.root / self.JOURNAL_DIR_NAME

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["git", f"--git-dir={self.git_dir}", f"--work-tree={self.root}", *args]
        try:
            return subprocess.run(cmd, check=check, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            raise JournalError(
                f"git {' '.join(args)} failed (exit {e.returncode}): {e.stderr.strip()}"
            ) from e

    def init(self) -> None:
        if self.git_dir.is_dir():
            return
        self.git_dir.mkdir(parents=True)
        self._git("init", "--quiet", "--initial-branch=main")
        self._git("config", "user.name", "konaclaw")
        self._git("config", "user.email", "konaclaw@local")
        self._git("config", "commit.gpgsign", "false")
        # Empty initial commit so revert always has a parent.
        self._git("commit", "--allow-empty", "--quiet", "-m", "init journal")

    def commit(self, message: str, author_agent: str, paths: Iterable[Path]) -> str:
        rel = [Path(p).resolve().relative_to(self.root).as_posix() for p in paths]
        # `git add --all -- <paths>` covers create, modify, AND delete.
        self._git("add", "--all", "--", *rel)
        # Per-call author override so the commit reflects which agent acted.
        self._git(
            "-c", f"user.name=konaclaw {author_agent}",
            "commit", "--allow-empty", "--quiet", "-m", message,
        )
        return self._git("rev-parse", "HEAD").stdout.strip()

    def revert(self, sha: str) -> str:
        """Revert the given commit. Returns the new commit's SHA."""
        self._git("revert", "--no-edit", sha)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def log(self) -> list[dict]:
        out = self._git("log", "--pretty=format:%H%x1f%an%x1f%s").stdout
        entries: list[dict] = []
        for line in out.splitlines():
            if not line:
                continue
            sha, author, msg = line.split("\x1f", 2)
            entries.append({"sha": sha, "message": msg, "author": author})
        return entries
