from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kc_attachments.store import AttachmentStore

from kc_supervisor.attachments_routes import build_attachments_router


def _app_with_router(tmp_path: Path):
    from fastapi import FastAPI
    store = AttachmentStore(root=tmp_path / "attachments")
    app = FastAPI()
    app.include_router(build_attachments_router(store=store))
    return app, store


def test_upload_text_file_success(tmp_path):
    app, store = _app_with_router(tmp_path)
    client = TestClient(app)
    files = {"file": ("hello.txt", b"Hello there.", "text/plain")}
    resp = client.post(
        "/attachments/upload",
        params={"conversation_id": "conv_1"},
        files=files,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["attachment_id"].startswith("att_")
    assert body["filename"] == "hello.txt"
    assert body["mime"] == "text/plain"
    assert body["parse_status"] == "ok"


def test_upload_rejects_oversize(tmp_path, monkeypatch):
    monkeypatch.setenv("KC_ATTACH_MAX_BYTES", "10")
    app, _store = _app_with_router(tmp_path)
    client = TestClient(app)
    files = {"file": ("big.txt", b"this is way too long", "text/plain")}
    resp = client.post(
        "/attachments/upload",
        params={"conversation_id": "conv_1"},
        files=files,
    )
    assert resp.status_code == 413


def test_upload_rejects_unknown_type(tmp_path):
    app, _store = _app_with_router(tmp_path)
    client = TestClient(app)
    files = {"file": ("evil.swf", b"random bytes", "application/x-shockwave-flash")}
    resp = client.post(
        "/attachments/upload",
        params={"conversation_id": "conv_1"},
        files=files,
    )
    assert resp.status_code == 415


def test_delete_removes_attachment(tmp_path):
    app, store = _app_with_router(tmp_path)
    client = TestClient(app)
    files = {"file": ("hello.txt", b"Hello.", "text/plain")}
    resp = client.post("/attachments/upload", params={"conversation_id": "conv_1"}, files=files)
    att_id = resp.json()["attachment_id"]

    resp_del = client.delete(f"/attachments/{att_id}")
    assert resp_del.status_code == 200

    from kc_attachments.store import AttachmentNotFound
    with pytest.raises(AttachmentNotFound):
        store.get(att_id)
