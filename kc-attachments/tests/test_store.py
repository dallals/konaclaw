from pathlib import Path

import pytest

from kc_attachments.store import AttachmentStore, AttachmentNotFound


@pytest.fixture
def store(tmp_path: Path) -> AttachmentStore:
    return AttachmentStore(root=tmp_path)


def test_save_returns_attachment_with_id(store, tmp_path):
    src = tmp_path / "hello.txt"
    src.write_text("Hello.", encoding="utf-8")
    att = store.save(conversation_id="conv_1", source=src, filename="hello.txt")
    assert att.id.startswith("att_")
    assert att.filename == "hello.txt"
    assert att.parse_status == "ok"
    assert att.mime == "text/plain"


def test_save_persists_original_and_parsed(store, tmp_path):
    src = tmp_path / "hello.txt"
    src.write_text("Hello.", encoding="utf-8")
    att = store.save(conversation_id="conv_1", source=src, filename="hello.txt")
    att_dir = store.attachment_dir(att.id)
    assert (att_dir / "original.txt").read_text(encoding="utf-8") == "Hello."
    assert "Hello." in (att_dir / "parsed.md").read_text(encoding="utf-8")


def test_get_returns_full_record(store, tmp_path):
    src = tmp_path / "hello.txt"
    src.write_text("Hello.", encoding="utf-8")
    att = store.save(conversation_id="conv_1", source=src, filename="hello.txt")
    got = store.get(att.id)
    assert got.id == att.id
    assert got.filename == "hello.txt"


def test_get_unknown_raises(store):
    with pytest.raises(AttachmentNotFound):
        store.get("att_doesnotexist")


def test_list_for_conversation_scopes(store, tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("A", encoding="utf-8")
    a = store.save(conversation_id="conv_1", source=src, filename="a.txt")
    src.write_text("B", encoding="utf-8")
    b = store.save(conversation_id="conv_2", source=src, filename="b.txt")
    listed = store.list_for_conversation("conv_1")
    assert [r.id for r in listed] == [a.id]
    listed2 = store.list_for_conversation("conv_2")
    assert [r.id for r in listed2] == [b.id]


def test_delete_removes_files_and_row(store, tmp_path):
    src = tmp_path / "hello.txt"
    src.write_text("Hello.", encoding="utf-8")
    att = store.save(conversation_id="conv_1", source=src, filename="hello.txt")
    att_dir = store.attachment_dir(att.id)
    store.delete(att.id)
    with pytest.raises(AttachmentNotFound):
        store.get(att.id)
    assert not att_dir.exists()


def test_parsed_md_capped_at_1mb(store, tmp_path):
    big = tmp_path / "big.txt"
    big.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")
    att = store.save(conversation_id="conv_1", source=big, filename="big.txt")
    parsed = store.read_parsed(att.id)
    assert len(parsed) <= 1 * 1024 * 1024


def test_evict_older_than_removes_old_and_keeps_recent(tmp_path):
    s = AttachmentStore(root=tmp_path)
    src = tmp_path / "h.txt"
    src.write_text("hello", encoding="utf-8")
    old = s.save(conversation_id="conv_1", source=src, filename="h.txt")
    # Backdate the row in sqlite to simulate an old attachment.
    s._db.execute(
        "UPDATE attachments SET parsed_at = ? WHERE id = ?",
        ("2024-01-01T00:00:00+00:00", old.id),
    )
    s._db.commit()
    recent = s.save(conversation_id="conv_1", source=src, filename="h.txt")

    evicted = s.evict_older_than(days=30)
    assert old.id in evicted
    assert recent.id not in evicted
    with pytest.raises(AttachmentNotFound):
        s.get(old.id)
    assert s.get(recent.id).id == recent.id
