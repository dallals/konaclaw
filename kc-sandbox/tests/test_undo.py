from pathlib import Path
import pytest
from kc_sandbox.shares import Share, SharesRegistry
from kc_sandbox.journal import Journal
from kc_sandbox.undo import UndoLog, Undoer, UndoEntry


@pytest.fixture
def share_with_journal(tmp_path):
    root = tmp_path / "research"
    root.mkdir()
    j = Journal(root); j.init()
    s = Share(name="research", path=root, mode="read-write")
    return SharesRegistry([s]), j, root


def test_undo_log_round_trip(tmp_path):
    log = UndoLog(db_path=tmp_path / "undo.db")
    log.init()
    eid = log.record(UndoEntry(
        agent="kc", tool="file.write", reverse_kind="git-revert",
        reverse_payload={"share": "research", "sha": "abc123"},
    ))
    assert eid > 0
    e = log.get(eid)
    assert e.reverse_kind == "git-revert"
    assert e.reverse_payload["sha"] == "abc123"


def test_undoer_reverts_a_recorded_entry(share_with_journal, tmp_path):
    shares, journal, root = share_with_journal
    f = root / "notes.md"
    f.write_text("v1\n")
    sha = journal.commit("v1", "kc", [f])
    log = UndoLog(db_path=tmp_path / "undo.db"); log.init()
    eid = log.record(UndoEntry(
        agent="kc", tool="file.write", reverse_kind="git-revert",
        reverse_payload={"share": "research", "sha": sha},
    ))
    undoer = Undoer(journals={"research": journal}, log=log)
    undoer.undo(eid)
    assert not f.exists()  # write is reverted -> file removed
    assert log.get(eid).applied_at is not None


def test_unknown_reverse_kind_raises(tmp_path, share_with_journal):
    shares, journal, root = share_with_journal
    log = UndoLog(db_path=tmp_path / "undo.db"); log.init()
    eid = log.record(UndoEntry(
        agent="kc", tool="external", reverse_kind="not-implemented-yet",
        reverse_payload={},
    ))
    undoer = Undoer(journals={"research": journal}, log=log)
    with pytest.raises(NotImplementedError):
        undoer.undo(eid)


def test_undo_already_applied_raises_value_error(share_with_journal, tmp_path):
    """A second undo() call on the same entry must raise ValueError, not silently
    re-revert (which would create a second revert commit on top of the first)."""
    shares, journal, root = share_with_journal
    f = root / "notes.md"
    f.write_text("v1\n")
    sha = journal.commit("v1", "kc", [f])
    log = UndoLog(db_path=tmp_path / "undo.db"); log.init()
    eid = log.record(UndoEntry(
        agent="kc", tool="file.write", reverse_kind="git-revert",
        reverse_payload={"share": "research", "sha": sha},
    ))
    undoer = Undoer(journals={"research": journal}, log=log)
    undoer.undo(eid)  # first call succeeds
    with pytest.raises(ValueError, match="already applied"):
        undoer.undo(eid)  # second call must raise


def test_undo_missing_journal_raises_key_error(share_with_journal, tmp_path):
    """If the entry references a share whose journal is not in the journals dict,
    Undoer.undo() must raise KeyError before attempting any revert."""
    shares, journal, root = share_with_journal
    log = UndoLog(db_path=tmp_path / "undo.db"); log.init()
    eid = log.record(UndoEntry(
        agent="kc", tool="file.write", reverse_kind="git-revert",
        reverse_payload={"share": "unknown_share", "sha": "abc123"},
    ))
    undoer = Undoer(journals={"research": journal}, log=log)  # only "research" journal
    with pytest.raises(KeyError, match="unknown_share"):
        undoer.undo(eid)
