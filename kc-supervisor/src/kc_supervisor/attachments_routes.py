from __future__ import annotations
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from kc_attachments.store import AttachmentNotFound, AttachmentStore
from kc_attachments.sniff import UnsupportedTypeError, sniff_mime


def _max_bytes() -> int:
    return int(os.environ.get("KC_ATTACH_MAX_BYTES", str(25 * 1024 * 1024)))


def _max_per_conv() -> int:
    return int(os.environ.get("KC_ATTACH_MAX_PER_CONV", str(500 * 1024 * 1024)))


def build_attachments_router(*, store: AttachmentStore) -> APIRouter:
    router = APIRouter(prefix="/attachments", tags=["attachments"])

    @router.post("/upload")
    async def upload(
        conversation_id: str = Query(...),
        file: UploadFile = File(...),
    ):
        body = await file.read()
        if len(body) > _max_bytes():
            raise HTTPException(status_code=413, detail="file too large")

        with tempfile.NamedTemporaryFile(
            delete=False, suffix=Path(file.filename or "").suffix
        ) as tf:
            tf.write(body)
            tmp_path = Path(tf.name)
        try:
            try:
                sniff_mime(tmp_path)
            except UnsupportedTypeError as e:
                raise HTTPException(status_code=415, detail=str(e))
            existing = store.list_for_conversation(conversation_id)
            total_bytes = sum(r.size_bytes for r in existing)
            if total_bytes + len(body) > _max_per_conv():
                raise HTTPException(
                    status_code=413,
                    detail=f"conversation attachment quota exceeded ({total_bytes + len(body)} > {_max_per_conv()})",
                )
            rec = store.save(
                conversation_id=conversation_id,
                source=tmp_path,
                filename=file.filename or "unnamed",
            )
        finally:
            tmp_path.unlink(missing_ok=True)

        snippet = store.read_parsed(rec.id)[:200]
        return {
            "attachment_id": rec.id,
            "filename": rec.filename,
            "mime": rec.mime,
            "size_bytes": rec.size_bytes,
            "parse_status": rec.parse_status,
            "parse_error": rec.parse_error,
            "snippet": snippet,
            "page_count": rec.page_count,
        }

    @router.delete("/{attachment_id}")
    def delete(attachment_id: str):
        try:
            store.delete(attachment_id)
        except AttachmentNotFound:
            raise HTTPException(status_code=404, detail="attachment not found")
        return {"ok": True}

    return router
