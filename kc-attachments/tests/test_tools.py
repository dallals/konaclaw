import json
from pathlib import Path

import pytest

from kc_attachments.store import AttachmentStore
from kc_attachments.tools import build_read_attachment_tool


def _store(tmp_path) -> AttachmentStore:
    return AttachmentStore(root=tmp_path / "attachments")


@pytest.fixture
def store_with_text(tmp_path):
    s = _store(tmp_path)
    src = tmp_path / "hello.txt"
    src.write_text("Hello attachment.", encoding="utf-8")
    rec = s.save(conversation_id="conv_1", source=src, filename="hello.txt")
    return s, rec.id


@pytest.mark.asyncio
async def test_read_attachment_returns_text_payload(store_with_text):
    s, att_id = store_with_text
    impl = build_read_attachment_tool(store=s, conversation_id="conv_1",
                                       vision_for_active_model=False)
    out = await impl({"attachment_id": att_id})
    parsed = json.loads(out)
    assert parsed["type"] == "text"
    assert "Hello attachment." in parsed["markdown"]


@pytest.mark.asyncio
async def test_read_attachment_rejects_other_conversation(store_with_text):
    s, att_id = store_with_text
    impl = build_read_attachment_tool(store=s, conversation_id="conv_99",
                                       vision_for_active_model=False)
    out = await impl({"attachment_id": att_id})
    parsed = json.loads(out)
    assert parsed["error"] == "out_of_scope"


@pytest.mark.asyncio
async def test_read_attachment_not_found(store_with_text):
    s, _ = store_with_text
    impl = build_read_attachment_tool(store=s, conversation_id="conv_1",
                                       vision_for_active_model=False)
    out = await impl({"attachment_id": "att_doesnotexist"})
    parsed = json.loads(out)
    assert parsed["error"] == "not_found"


@pytest.mark.asyncio
async def test_read_attachment_truncates_long_text(tmp_path):
    s = _store(tmp_path)
    src = tmp_path / "long.txt"
    src.write_text("x" * (64 * 1024), encoding="utf-8")
    rec = s.save(conversation_id="conv_1", source=src, filename="long.txt")
    impl = build_read_attachment_tool(store=s, conversation_id="conv_1",
                                       vision_for_active_model=False)
    out = await impl({"attachment_id": rec.id})
    parsed = json.loads(out)
    assert len(parsed["markdown"].encode("utf-8")) <= 32 * 1024 + 200
    assert "[truncated" in parsed["markdown"]


@pytest.mark.asyncio
async def test_read_attachment_paginates_pdf_by_page_range(tmp_path):
    s = _store(tmp_path)
    src = tmp_path / "fake.pdf"
    src.write_bytes(b"")  # empty bytes — parser will fail; we'll overwrite parsed.md below
    rec = s.save(conversation_id="conv_1", source=src, filename="fake.pdf")
    parsed_path = s.attachment_dir(rec.id) / "parsed.md"
    parsed_path.write_text(
        "## Page 1\n\nFirst.\n\n## Page 2\n\nSecond.\n\n## Page 3\n\nThird.",
        encoding="utf-8",
    )
    # Overwrite parse_status in the index to 'ok' so the tool doesn't short-circuit on parse_error.
    s._db.execute("UPDATE attachments SET parse_status = 'ok', mime = 'application/pdf' WHERE id = ?", (rec.id,))
    s._db.commit()

    impl = build_read_attachment_tool(store=s, conversation_id="conv_1",
                                       vision_for_active_model=False)
    out = await impl({"attachment_id": rec.id, "page_range": "2-3"})
    parsed = json.loads(out)
    assert "Page 2" in parsed["markdown"]
    assert "Page 3" in parsed["markdown"]
    assert "First." not in parsed["markdown"]
