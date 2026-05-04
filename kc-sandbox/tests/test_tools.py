from pathlib import Path
import pytest
from kc_sandbox.shares import Share, SharesRegistry, ShareError
from kc_sandbox.journal import Journal
from kc_sandbox.undo import UndoLog
from kc_sandbox.tools import build_file_tools


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "research"
    root.mkdir()
    j = Journal(root); j.init()
    shares = SharesRegistry([Share("research", root, "read-write")])
    log = UndoLog(tmp_path / "u.db"); log.init()
    journals = {"research": j}
    tools = build_file_tools(shares=shares, journals=journals, undo_log=log, agent_name="kc")
    return shares, j, log, tools, root


def test_file_write_creates_file_and_journals(env):
    _, j, log, tools, root = env
    res = tools["file.write"].impl(share="research", relpath="x.md", content="hi\n")
    assert "wrote" in res
    assert (root / "x.md").read_text() == "hi\n"
    assert len(j.log()) == 2  # init + this write


def test_file_read_returns_content(env):
    _, _, _, tools, root = env
    (root / "x.md").write_text("hello\n")
    res = tools["file.read"].impl(share="research", relpath="x.md")
    assert "hello" in res


def test_file_list_lists_files(env):
    _, _, _, tools, root = env
    (root / "a.md").write_text("a")
    (root / "b.md").write_text("b")
    res = tools["file.list"].impl(share="research", relpath=".")
    assert "a.md" in res and "b.md" in res
    assert ".kc-journal" not in res  # never expose the journal dir


def test_file_delete_removes_and_journals_and_logs_undo(env):
    _, j, log, tools, root = env
    (root / "x.md").write_text("hello\n")
    j.commit("create x", "kc", [root / "x.md"])
    res = tools["file.delete"].impl(share="research", relpath="x.md")
    assert "deleted" in res
    assert not (root / "x.md").exists()
    # An UndoLog entry should exist
    e = log.get(1)
    assert e.reverse_kind == "git-revert"


def test_file_write_to_readonly_share_raises(tmp_path):
    root = tmp_path / "ro"; root.mkdir()
    Journal(root).init()
    shares = SharesRegistry([Share("ro", root, "read-only")])
    log = UndoLog(tmp_path / "u.db"); log.init()
    tools = build_file_tools(shares, {"ro": Journal(root)}, log, agent_name="kc")
    with pytest.raises(ShareError, match="read-only"):
        tools["file.write"].impl(share="ro", relpath="x", content="y")


def test_path_traversal_blocked(env):
    _, _, _, tools, _ = env
    with pytest.raises(ShareError, match="escapes"):
        tools["file.read"].impl(share="research", relpath="../etc/passwd")
