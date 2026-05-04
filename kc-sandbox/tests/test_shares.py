from pathlib import Path
import pytest
from kc_sandbox.shares import Share, SharesRegistry, ShareError


def test_resolve_inside_share(tmp_path):
    (tmp_path / "research").mkdir()
    s = Share(name="research", path=tmp_path / "research", mode="read-write")
    r = SharesRegistry([s])
    p = r.resolve("research", "notes/today.md")
    assert p == (tmp_path / "research" / "notes" / "today.md").resolve()


def test_resolve_unknown_share(tmp_path):
    r = SharesRegistry([])
    with pytest.raises(ShareError, match="unknown share"):
        r.resolve("nope", "x.txt")


def test_resolve_rejects_dotdot(tmp_path):
    (tmp_path / "research").mkdir()
    s = Share(name="research", path=tmp_path / "research", mode="read-write")
    r = SharesRegistry([s])
    with pytest.raises(ShareError, match="escapes share"):
        r.resolve("research", "../secrets.txt")


def test_resolve_rejects_absolute_relpath(tmp_path):
    (tmp_path / "research").mkdir()
    s = Share(name="research", path=tmp_path / "research", mode="read-write")
    r = SharesRegistry([s])
    with pytest.raises(ShareError, match="must be relative"):
        r.resolve("research", "/etc/passwd")


def test_resolve_rejects_symlink_escape(tmp_path):
    (tmp_path / "research").mkdir()
    (tmp_path / "outside.txt").write_text("secret")
    (tmp_path / "research" / "link").symlink_to(tmp_path / "outside.txt")
    s = Share(name="research", path=tmp_path / "research", mode="read-write")
    r = SharesRegistry([s])
    with pytest.raises(ShareError, match="escapes share"):
        r.resolve("research", "link")


def test_can_write_respects_mode(tmp_path):
    (tmp_path / "ro").mkdir()
    s = Share(name="ro", path=tmp_path / "ro", mode="read-only")
    r = SharesRegistry([s])
    assert r.can_write("ro") is False
    assert r.can_read("ro") is True


def test_load_from_yaml(tmp_path):
    cfg = tmp_path / "shares.yaml"
    (tmp_path / "research").mkdir()
    cfg.write_text(f"""
shares:
  - name: research
    path: {tmp_path / 'research'}
    mode: read-write
""")
    r = SharesRegistry.from_yaml(cfg)
    assert r.resolve("research", "x.md") == (tmp_path / "research" / "x.md").resolve()
