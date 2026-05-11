import pytest
from pathlib import Path
from kc_terminal.paths import (
    validate_cwd,
    CwdNotAbsolute,
    CwdDoesNotExist,
    CwdNotADirectory,
    CwdOutsideRoots,
)


@pytest.fixture
def workdir(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "subdir"
    inside.mkdir()
    a_file = inside / "f.txt"
    a_file.write_text("x")
    outside = tmp_path / "outside"
    outside.mkdir()
    return {"root": root, "inside": inside, "file": a_file, "outside": outside, "tmp": tmp_path}


def test_inside_root_is_accepted(workdir):
    out = validate_cwd(str(workdir["inside"]), [workdir["root"]])
    assert out == workdir["inside"].resolve()


def test_root_itself_is_accepted(workdir):
    out = validate_cwd(str(workdir["root"]), [workdir["root"]])
    assert out == workdir["root"].resolve()


def test_relative_path_rejected(workdir):
    with pytest.raises(CwdNotAbsolute):
        validate_cwd("subdir", [workdir["root"]])


def test_nonexistent_path_rejected(workdir):
    missing = workdir["root"] / "no-such-dir"
    with pytest.raises(CwdDoesNotExist):
        validate_cwd(str(missing), [workdir["root"]])


def test_file_not_a_directory(workdir):
    with pytest.raises(CwdNotADirectory):
        validate_cwd(str(workdir["file"]), [workdir["root"]])


def test_outside_roots_rejected(workdir):
    with pytest.raises(CwdOutsideRoots):
        validate_cwd(str(workdir["outside"]), [workdir["root"]])


def test_symlink_to_outside_rejected(workdir):
    link = workdir["root"] / "escape"
    link.symlink_to(workdir["outside"])
    with pytest.raises(CwdOutsideRoots):
        validate_cwd(str(link), [workdir["root"]])


def test_symlink_to_inside_accepted(workdir):
    link = workdir["outside"] / "shortcut"
    link.symlink_to(workdir["inside"])
    out = validate_cwd(str(link), [workdir["root"]])
    assert out == workdir["inside"].resolve()


def test_multi_root_second_match(workdir, tmp_path):
    other = tmp_path / "other-root"
    other.mkdir()
    sub = other / "x"
    sub.mkdir()
    out = validate_cwd(str(sub), [workdir["root"], other])
    assert out == sub.resolve()
