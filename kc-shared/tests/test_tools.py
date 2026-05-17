import json

import pytest

from kc_core.tools import ToolRegistry
from kc_shared.store import (
    SharedFileNotFound,
    SharedPathOutOfScope,
    SharedStore,
)
from kc_shared.tools import (
    build_list_shared_files_tool,
    build_read_shared_file_tool,
    build_write_shared_file_tool,
)
from kc_shared.wiring import attach_shared_to_agent


def _store(tmp_path) -> SharedStore:
    s = SharedStore(root=tmp_path / "KonaShared")
    s.ensure_dirs()
    return s


# ---------------------------------------------------------------------- store


def test_ensure_dirs_creates_both_subtrees(tmp_path):
    s = SharedStore(root=tmp_path / "KonaShared")
    s.ensure_dirs()
    assert (s.root / "originals").is_dir()
    assert (s.root / "kona-edits").is_dir()


def test_edits_dir_for_creates_lazily(tmp_path):
    s = _store(tmp_path)
    unmaterialized = s.edits_dir_for("42", create=False)
    assert not unmaterialized.exists()
    materialized = s.edits_dir_for("42", create=True)
    assert materialized.exists()
    assert materialized.name.endswith("-conv42")
    # Subsequent lookups (with or without create) return the same folder.
    same = s.edits_dir_for("42", create=False)
    assert same == materialized
    same2 = s.edits_dir_for("42", create=True)
    assert same2 == materialized


def test_write_edit_rejects_path_traversal(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(SharedPathOutOfScope):
        s.write_edit(conversation_id="1", filename="../escape.txt", content=b"x")
    with pytest.raises(SharedPathOutOfScope):
        s.write_edit(conversation_id="1", filename="sub/inside.txt", content=b"x")
    with pytest.raises(SharedPathOutOfScope):
        s.write_edit(conversation_id="1", filename="", content=b"x")
    with pytest.raises(SharedPathOutOfScope):
        s.write_edit(conversation_id="1", filename="weird;name.txt", content=b"x")


def test_read_file_blocks_path_escape(tmp_path):
    s = _store(tmp_path)
    # Plant a file *outside* the shared root. Root is tmp_path/KonaShared, so
    # "../../secret.txt" from originals/ would land at tmp_path/secret.txt.
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    with pytest.raises(SharedPathOutOfScope):
        s.read_file("../../secret.txt")
    # Also rejected when prefixed with a recognized folder name.
    with pytest.raises(SharedPathOutOfScope):
        s.read_file("originals/../../secret.txt")


def test_read_file_finds_originals_with_bare_name(tmp_path):
    s = _store(tmp_path)
    (s.originals_dir() / "resume.pdf").write_bytes(b"%PDF-1.4 fake")
    data, abspath = s.read_file("resume.pdf")
    assert data.startswith(b"%PDF")
    assert abspath == s.originals_dir() / "resume.pdf"


def test_read_file_blocks_cross_conversation_edits(tmp_path):
    s = _store(tmp_path)
    edits_a = s.edits_dir_for("1", create=True)
    (edits_a / "mine.md").write_text("A", encoding="utf-8")
    # conv 2 tries to read conv 1's file by its actual path.
    rel = f"kona-edits/{edits_a.name}/mine.md"
    with pytest.raises(SharedPathOutOfScope):
        s.read_file(rel, conversation_id="2")
    # conv 1 reads its own file fine.
    data, _ = s.read_file(rel, conversation_id="1")
    assert data == b"A"


# ----------------------------------------------------------------------- tools


@pytest.mark.asyncio
async def test_list_originals_returns_planted_files(tmp_path):
    s = _store(tmp_path)
    (s.originals_dir() / "a.txt").write_text("alpha", encoding="utf-8")
    (s.originals_dir() / "nested").mkdir()
    (s.originals_dir() / "nested" / "b.txt").write_text("beta", encoding="utf-8")
    impl = build_list_shared_files_tool(store=s, conversation_id="1")
    out = json.loads(await impl(folder="originals"))
    assert out["folder"] == "originals"
    paths = {f["path"] for f in out["files"]}
    assert paths == {"a.txt", "nested/b.txt"}


@pytest.mark.asyncio
async def test_list_rejects_bad_folder(tmp_path):
    s = _store(tmp_path)
    impl = build_list_shared_files_tool(store=s, conversation_id="1")
    out = json.loads(await impl(folder="../etc"))
    assert out["error"] == "bad_folder"


@pytest.mark.asyncio
async def test_read_shared_text_returns_inline(tmp_path):
    s = _store(tmp_path)
    (s.originals_dir() / "notes.md").write_text("# Hello\n", encoding="utf-8")
    impl = build_read_shared_file_tool(store=s, conversation_id="1")
    out = json.loads(await impl(path="notes.md"))
    assert out["type"] == "text"
    assert "Hello" in out["content"]


@pytest.mark.asyncio
async def test_read_shared_pdf_parses_inline(tmp_path):
    # Build a minimal real PDF so PdfReader actually walks pages.
    from pypdf import PdfWriter
    pdf_path = _store(tmp_path).originals_dir() / "doc.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as f:
        writer.write(f)
    s = SharedStore(root=pdf_path.parent.parent)
    impl = build_read_shared_file_tool(store=s, conversation_id="1")
    out = json.loads(await impl(path="doc.pdf"))
    assert out["type"] == "text"
    assert out["mime"] == "application/pdf"
    assert "## Page 1" in out["content"]
    assert "## Page 2" in out["content"]


@pytest.mark.asyncio
async def test_read_shared_pdf_page_range(tmp_path):
    from pypdf import PdfWriter
    pdf_path = _store(tmp_path).originals_dir() / "doc.pdf"
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as f:
        writer.write(f)
    s = SharedStore(root=pdf_path.parent.parent)
    impl = build_read_shared_file_tool(store=s, conversation_id="1")
    out = json.loads(await impl(path="doc.pdf", page_range="2-3"))
    assert "## Page 2" in out["content"]
    assert "## Page 3" in out["content"]
    assert "## Page 1" not in out["content"]


@pytest.mark.asyncio
async def test_read_shared_unknown_binary_returns_sentinel(tmp_path):
    s = _store(tmp_path)
    # A random binary blob with no recognized magic and no parser hit.
    (s.originals_dir() / "blob.bin").write_bytes(b"\x00\x01\x02\xff\xfe garbage")
    impl = build_read_shared_file_tool(store=s, conversation_id="1")
    out = json.loads(await impl(path="blob.bin"))
    assert out["type"] == "binary"
    assert out["path"].endswith("blob.bin")


@pytest.mark.asyncio
async def test_read_shared_missing_returns_not_found(tmp_path):
    s = _store(tmp_path)
    impl = build_read_shared_file_tool(store=s, conversation_id="1")
    out = json.loads(await impl(path="ghost.txt"))
    assert out["error"] == "not_found"


@pytest.mark.asyncio
async def test_write_shared_creates_lazy_folder_and_file(tmp_path):
    s = _store(tmp_path)
    impl = build_write_shared_file_tool(store=s, conversation_id="7")
    out = json.loads(await impl(filename="hello.md", content="hi sammy"))
    assert out["type"] == "written"
    assert "kona-edits" in out["path"]
    assert out["path"].endswith("hello.md")

    # Verify the file actually landed and is readable via the read tool.
    read = build_read_shared_file_tool(store=s, conversation_id="7")
    payload = json.loads(await read(path=out["path"].split("KonaShared/")[-1]))
    assert payload["type"] == "text"
    assert payload["content"].strip() == "hi sammy"


@pytest.mark.asyncio
async def test_write_shared_rejects_traversal(tmp_path):
    s = _store(tmp_path)
    impl = build_write_shared_file_tool(store=s, conversation_id="1")
    out = json.loads(await impl(filename="../escape.txt", content="bad"))
    assert out["error"] == "out_of_scope"


@pytest.mark.asyncio
async def test_write_then_list_edits_is_conversation_scoped(tmp_path):
    s = _store(tmp_path)
    await build_write_shared_file_tool(store=s, conversation_id="1")(
        filename="a.md", content="A"
    )
    await build_write_shared_file_tool(store=s, conversation_id="2")(
        filename="b.md", content="B"
    )
    out1 = json.loads(
        await build_list_shared_files_tool(store=s, conversation_id="1")(folder="kona-edits")
    )
    out2 = json.loads(
        await build_list_shared_files_tool(store=s, conversation_id="2")(folder="kona-edits")
    )
    paths1 = {f["path"] for f in out1["files"]}
    paths2 = {f["path"] for f in out2["files"]}
    assert paths1 == {"a.md"}
    assert paths2 == {"b.md"}


# ---------------------------------------------------------------------- wiring


def test_attach_shared_to_agent_registers_three_tools(tmp_path):
    s = _store(tmp_path)
    reg = ToolRegistry()
    attach_shared_to_agent(registry=reg, store=s, conversation_id="1")
    assert set(reg.names()) == {
        "list_shared_files",
        "read_shared_file",
        "write_shared_file",
    }
