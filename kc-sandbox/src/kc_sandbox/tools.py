from __future__ import annotations
from pathlib import Path
from kc_core.tools import Tool
from kc_sandbox.journal import Journal
from kc_sandbox.permissions import Tier
from kc_sandbox.shares import SharesRegistry, ShareError
from kc_sandbox.undo import UndoEntry, UndoLog


def build_file_tools(
    shares: SharesRegistry,
    journals: dict[str, Journal],
    undo_log: UndoLog,
    agent_name: str,
) -> dict[str, Tool]:
    """Construct a set of sandboxed file.* tools bound to a specific agent."""

    def _journal_for(share: str) -> Journal:
        j = journals.get(share)
        if j is None:
            raise ShareError(f"no journal configured for share {share!r}")
        return j

    # ---- READ ----
    def file_read(share: str, relpath: str) -> str:
        p = shares.resolve(share, relpath)
        if not p.is_file():
            raise ShareError(f"{relpath}: not a file in share {share!r}")
        try:
            return p.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            raise ShareError(f"{relpath}: not valid UTF-8 text in share {share!r}") from e

    # ---- LIST ----
    def file_list(share: str, relpath: str = ".") -> str:
        p = shares.resolve(share, relpath)
        if not p.is_dir():
            raise ShareError(f"{relpath}: not a directory in share {share!r}")
        names = sorted([
            x.name + ("/" if x.is_dir() else "")
            for x in p.iterdir()
            if x.name != Journal.JOURNAL_DIR_NAME
        ])
        return "\n".join(names)

    # ---- WRITE ----
    def file_write(share: str, relpath: str, content: str) -> str:
        if not shares.can_write(share):
            raise ShareError(f"share {share!r} is read-only")
        p = shares.resolve(share, relpath)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        sha = _journal_for(share).commit(
            message=f"file.write {share}/{relpath}",
            author_agent=agent_name,
            paths=[p],
        )
        undo_log.record(UndoEntry(
            agent=agent_name, tool="file.write",
            reverse_kind="git-revert",
            reverse_payload={"share": share, "sha": sha},
        ))
        return f"wrote {len(content)} bytes to {share}/{relpath} (commit {sha[:7]})"

    # ---- DELETE ----
    def file_delete(share: str, relpath: str) -> str:
        if not shares.can_write(share):
            raise ShareError(f"share {share!r} is read-only")
        p = shares.resolve(share, relpath)
        if not p.is_file():
            raise ShareError(f"{relpath}: not a file in share {share!r}")
        p.unlink()
        sha = _journal_for(share).commit(
            message=f"file.delete {share}/{relpath}",
            author_agent=agent_name,
            paths=[p],
        )
        undo_log.record(UndoEntry(
            agent=agent_name, tool="file.delete",
            reverse_kind="git-revert",
            reverse_payload={"share": share, "sha": sha},
        ))
        return f"deleted {share}/{relpath} (commit {sha[:7]})"

    return {
        "file.read": Tool(
            name="file.read",
            description="Read a UTF-8 text file from inside a share.",
            parameters={
                "type": "object",
                "properties": {
                    "share": {"type": "string"},
                    "relpath": {"type": "string"},
                },
                "required": ["share", "relpath"],
            },
            impl=file_read,
        ),
        "file.list": Tool(
            name="file.list",
            description="List entries in a directory inside a share.",
            parameters={
                "type": "object",
                "properties": {
                    "share": {"type": "string"},
                    "relpath": {"type": "string", "default": "."},
                },
                "required": ["share"],
            },
            impl=file_list,
        ),
        "file.write": Tool(
            name="file.write",
            description="Write a UTF-8 text file inside a share. Overwrites if it exists.",
            parameters={
                "type": "object",
                "properties": {
                    "share": {"type": "string"},
                    "relpath": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["share", "relpath", "content"],
            },
            impl=file_write,
        ),
        "file.delete": Tool(
            name="file.delete",
            description="Delete a file inside a share. Destructive — requires approval.",
            parameters={
                "type": "object",
                "properties": {
                    "share": {"type": "string"},
                    "relpath": {"type": "string"},
                },
                "required": ["share", "relpath"],
            },
            impl=file_delete,
        ),
    }


# Tier mapping for these tools — consumed by PermissionEngine
DEFAULT_FILE_TOOL_TIERS: dict[str, Tier] = {
    "file.read":   Tier.SAFE,
    "file.list":   Tier.SAFE,
    "file.write":  Tier.MUTATING,
    "file.delete": Tier.DESTRUCTIVE,
}
