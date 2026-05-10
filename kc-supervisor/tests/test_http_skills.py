from __future__ import annotations
from pathlib import Path

from fastapi.testclient import TestClient

from kc_skills import SkillIndex


def _seed(skills_root: Path, name: str, **fm) -> None:
    sdir = skills_root / name
    sdir.mkdir(parents=True)
    extras = "".join(f"{k}: {v}\n" for k, v in fm.items())
    (sdir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: D\n{extras}---\n\n# Body\n\n"
    )


def test_get_skills_503_when_index_missing(deps, app):
    deps.skill_index = None
    with TestClient(app) as client:
        r = client.get("/skills")
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "skill_index_unavailable"


def test_get_skills_returns_list(deps, app, tmp_path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _seed(skills_root, "hello")
    _seed(skills_root, "goodbye")
    deps.skill_index = SkillIndex(skills_root)

    with TestClient(app) as client:
        r = client.get("/skills")
        assert r.status_code == 200
        body = r.json()
        names = {s["name"] for s in body["skills"]}
        assert names == {"hello", "goodbye"}


def test_get_skill_detail_200(deps, app, tmp_path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _seed(skills_root, "hello")
    deps.skill_index = SkillIndex(skills_root)

    with TestClient(app) as client:
        r = client.get("/skills/hello")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "hello"
        assert "body" in body
        assert "supporting_files" in body


def test_get_skill_detail_404(deps, app, tmp_path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    deps.skill_index = SkillIndex(skills_root)

    with TestClient(app) as client:
        r = client.get("/skills/nope")
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "skill_not_found"


def test_get_skill_file_200(deps, app, tmp_path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _seed(skills_root, "hello")
    (skills_root / "hello" / "references").mkdir()
    (skills_root / "hello" / "references" / "doc.md").write_text("ref body")
    deps.skill_index = SkillIndex(skills_root)

    with TestClient(app) as client:
        r = client.get("/skills/hello/files/references/doc.md")
        assert r.status_code == 200
        assert r.json()["content"] == "ref body"


def test_get_skill_file_404_missing(deps, app, tmp_path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _seed(skills_root, "hello")
    deps.skill_index = SkillIndex(skills_root)

    with TestClient(app) as client:
        r = client.get("/skills/hello/files/references/nope.md")
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "file_not_found"


def test_get_skill_file_422_path_escape(deps, app, tmp_path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _seed(skills_root, "hello")
    deps.skill_index = SkillIndex(skills_root)

    with TestClient(app) as client:
        r = client.get("/skills/hello/files/..%2Fsecret.txt")
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "path_outside_skill_dir"
