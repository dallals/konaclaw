from pathlib import Path

import pytest

from kc_attachments.store import AttachmentStore

from kc_supervisor.ws_routes import build_user_message_with_attachments


def test_text_attachment_prefixes_chip_line(tmp_path):
    store = AttachmentStore(root=tmp_path)
    src = tmp_path / "hello.txt"
    src.write_text("Hello.", encoding="utf-8")
    rec = store.save(conversation_id="conv_1", source=src, filename="hello.txt")

    msg = build_user_message_with_attachments(
        store=store,
        conversation_id="conv_1",
        text="What's in this?",
        attachment_ids=[rec.id],
    )
    assert "[attached: hello.txt" in msg.content
    assert f"id={rec.id}" in msg.content
    assert "What's in this?" in msg.content
    assert msg.images == ()


def test_image_attachment_adds_image_ref(tmp_path, monkeypatch):
    monkeypatch.setenv("KC_ATTACH_IMAGE_MODE", "eager")
    store = AttachmentStore(root=tmp_path)
    from PIL import Image
    src = tmp_path / "p.png"
    Image.new("RGB", (5, 5), "blue").save(src)
    rec = store.save(conversation_id="conv_1", source=src, filename="p.png")

    msg = build_user_message_with_attachments(
        store=store,
        conversation_id="conv_1",
        text="What is this?",
        attachment_ids=[rec.id],
    )
    assert "[attached: p.png" in msg.content
    assert len(msg.images) == 1
    assert msg.images[0].path == store.original_path(rec.id)
    assert msg.images[0].mime == "image/png"


def test_unknown_attachment_skipped(tmp_path):
    store = AttachmentStore(root=tmp_path)
    msg = build_user_message_with_attachments(
        store=store,
        conversation_id="conv_1",
        text="hi",
        attachment_ids=["att_doesnotexist"],
    )
    assert msg.content == "hi"
    assert msg.images == ()


def test_attachment_from_other_conversation_ignored(tmp_path):
    store = AttachmentStore(root=tmp_path)
    src = tmp_path / "h.txt"
    src.write_text("H", encoding="utf-8")
    rec = store.save(conversation_id="conv_OTHER", source=src, filename="h.txt")

    msg = build_user_message_with_attachments(
        store=store,
        conversation_id="conv_1",
        text="hi",
        attachment_ids=[rec.id],
    )
    assert msg.content == "hi"
    assert msg.images == ()


def test_lazy_mode_skips_image_refs(tmp_path, monkeypatch):
    monkeypatch.setenv("KC_ATTACH_IMAGE_MODE", "lazy")
    store = AttachmentStore(root=tmp_path)
    from PIL import Image
    src = tmp_path / "p.png"
    Image.new("RGB", (5, 5), "blue").save(src)
    rec = store.save(conversation_id="conv_1", source=src, filename="p.png")

    msg = build_user_message_with_attachments(
        store=store,
        conversation_id="conv_1",
        text="hi",
        attachment_ids=[rec.id],
    )
    assert msg.images == ()  # lazy mode: chip line only, image fetched via tool
    assert "[attached: p.png" in msg.content
