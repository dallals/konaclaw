from pathlib import Path
import pytest
from kc_sandbox.journal import Journal


def test_init_creates_journal_dir(tmp_path):
    j = Journal(share_root=tmp_path)
    j.init()
    assert (tmp_path / ".kc-journal").is_dir()
    assert (tmp_path / ".kc-journal" / "HEAD").is_file()


def test_init_idempotent(tmp_path):
    Journal(tmp_path).init()
    Journal(tmp_path).init()  # second call must not raise


def test_commit_records_a_write(tmp_path):
    j = Journal(share_root=tmp_path)
    j.init()
    f = tmp_path / "notes.md"
    f.write_text("hello\n")
    sha = j.commit(message="wrote notes.md", author_agent="kc", paths=[f])
    assert isinstance(sha, str) and len(sha) >= 7
    assert "wrote notes.md" in j.log()[0]["message"]


def test_revert_restores_previous_content(tmp_path):
    j = Journal(share_root=tmp_path)
    j.init()
    f = tmp_path / "notes.md"
    f.write_text("v1\n")
    sha1 = j.commit(message="v1", author_agent="kc", paths=[f])
    f.write_text("v2\n")
    sha2 = j.commit(message="v2", author_agent="kc", paths=[f])
    j.revert(sha2)
    assert f.read_text() == "v1\n"


def test_revert_restores_deleted_file(tmp_path):
    j = Journal(share_root=tmp_path)
    j.init()
    f = tmp_path / "notes.md"
    f.write_text("hello\n")
    j.commit(message="create", author_agent="kc", paths=[f])
    f.unlink()
    sha_del = j.commit(message="delete", author_agent="kc", paths=[f])
    j.revert(sha_del)
    assert f.read_text() == "hello\n"


def test_journal_dir_excluded_from_listing(tmp_path):
    j = Journal(share_root=tmp_path)
    j.init()
    assert ".kc-journal" not in [p.name for p in tmp_path.iterdir() if p.name != ".kc-journal"]
