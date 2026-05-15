from __future__ import annotations
import json
import secrets
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sniff import sniff_mime, dispatch_parser, UnsupportedTypeError


class AttachmentNotFound(Exception):
    pass


@dataclass(frozen=True)
class AttachmentRecord:
    id: str
    conversation_id: str
    filename: str
    mime: str
    size_bytes: int
    parse_status: str        # "ok" | "error"
    parse_error: str | None
    page_count: int | None
    parsed_at: str           # ISO 8601
    extra_meta: dict[str, Any]   # lives in meta.json, not sqlite


_SCHEMA = """
CREATE TABLE IF NOT EXISTS attachments (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    filename        TEXT NOT NULL,
    mime            TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    parse_status    TEXT NOT NULL,
    parse_error     TEXT,
    page_count      INTEGER,
    parsed_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attachments_conv ON attachments(conversation_id);
"""


def _gen_id() -> str:
    return "att_" + secrets.token_hex(6)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AttachmentStore:
    """Filesystem store at `root/<conv>/<att>/` with a sqlite index at `root/index.sqlite`."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(root / "index.sqlite"), check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def attachment_dir(self, attachment_id: str) -> Path:
        row = self._db.execute(
            "SELECT conversation_id FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        if row is None:
            raise AttachmentNotFound(attachment_id)
        return self._root / row[0] / attachment_id

    def save(
        self,
        *,
        conversation_id: str,
        source: Path,
        filename: str,
    ) -> AttachmentRecord:
        att_id = _gen_id()
        att_dir = self._root / conversation_id / att_id
        att_dir.mkdir(parents=True, exist_ok=False)

        ext = Path(filename).suffix or ".bin"
        original_path = att_dir / f"original{ext}"
        shutil.copy(source, original_path)

        size_bytes = original_path.stat().st_size
        parse_status = "ok"
        parse_error: str | None = None
        page_count: int | None = None
        extra_meta: dict[str, Any] = {}
        try:
            mime = sniff_mime(original_path)
            parser = dispatch_parser(mime)
            r = parser.parse(original_path, {})
            markdown = r.markdown
            extra_meta = dict(r.extra_meta)
            if len(markdown.encode("utf-8")) > 1 * 1024 * 1024:
                markdown = markdown.encode("utf-8")[:1 * 1024 * 1024].decode(
                    "utf-8", errors="ignore"
                )
                extra_meta["truncated_at"] = 1 * 1024 * 1024
            (att_dir / "parsed.md").write_text(markdown, encoding="utf-8")
            page_count = extra_meta.get("page_count")
        except UnsupportedTypeError as e:
            parse_status = "error"
            parse_error = str(e)
            mime = "application/octet-stream"
            (att_dir / "parsed.md").write_text("", encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            parse_status = "error"
            parse_error = f"{type(e).__name__}: {e}"
            mime = "application/octet-stream"
            (att_dir / "parsed.md").write_text("", encoding="utf-8")

        parsed_at = _now_iso()
        meta_doc = {
            "id": att_id,
            "conversation_id": conversation_id,
            "filename": filename,
            "mime": mime,
            "size_bytes": size_bytes,
            "parse_status": parse_status,
            "parse_error": parse_error,
            "page_count": page_count,
            "parsed_at": parsed_at,
            "extra_meta": extra_meta,
        }
        (att_dir / "meta.json").write_text(json.dumps(meta_doc, indent=2), encoding="utf-8")

        self._db.execute(
            "INSERT INTO attachments "
            "(id, conversation_id, filename, mime, size_bytes, parse_status, parse_error, page_count, parsed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (att_id, conversation_id, filename, mime, size_bytes,
             parse_status, parse_error, page_count, parsed_at),
        )
        self._db.commit()

        return AttachmentRecord(
            id=att_id, conversation_id=conversation_id, filename=filename,
            mime=mime, size_bytes=size_bytes, parse_status=parse_status,
            parse_error=parse_error, page_count=page_count, parsed_at=parsed_at,
            extra_meta=extra_meta,
        )

    def get(self, attachment_id: str) -> AttachmentRecord:
        row = self._db.execute(
            "SELECT id, conversation_id, filename, mime, size_bytes, "
            "parse_status, parse_error, page_count, parsed_at "
            "FROM attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()
        if row is None:
            raise AttachmentNotFound(attachment_id)
        att_dir = self._root / row[1] / row[0]
        meta_path = att_dir / "meta.json"
        extra_meta: dict[str, Any] = {}
        if meta_path.exists():
            try:
                extra_meta = json.loads(meta_path.read_text(encoding="utf-8")).get("extra_meta", {})
            except json.JSONDecodeError:
                extra_meta = {}
        return AttachmentRecord(
            id=row[0], conversation_id=row[1], filename=row[2], mime=row[3],
            size_bytes=row[4], parse_status=row[5], parse_error=row[6],
            page_count=row[7], parsed_at=row[8], extra_meta=extra_meta,
        )

    def list_for_conversation(self, conversation_id: str) -> list[AttachmentRecord]:
        rows = self._db.execute(
            "SELECT id FROM attachments WHERE conversation_id = ? ORDER BY parsed_at",
            (conversation_id,),
        ).fetchall()
        return [self.get(r[0]) for r in rows]

    def delete(self, attachment_id: str) -> None:
        att_dir = self.attachment_dir(attachment_id)
        if att_dir.exists():
            shutil.rmtree(att_dir)
        self._db.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
        self._db.commit()

    def read_parsed(self, attachment_id: str) -> str:
        att_dir = self.attachment_dir(attachment_id)
        parsed = att_dir / "parsed.md"
        if not parsed.exists():
            return ""
        return parsed.read_text(encoding="utf-8")

    def original_path(self, attachment_id: str) -> Path:
        att_dir = self.attachment_dir(attachment_id)
        for p in att_dir.iterdir():
            if p.name.startswith("original."):
                return p
        raise AttachmentNotFound(f"{attachment_id}: original file missing")
