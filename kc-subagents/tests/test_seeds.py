from pathlib import Path

from kc_subagents.seeds.install import install_seeds_if_empty


def test_installs_all_four_into_empty_dir(tmp_path: Path):
    installed = install_seeds_if_empty(tmp_path)
    assert sorted(installed) == ["coder", "email-drafter", "scheduler", "web-researcher"]
    assert (tmp_path / "web-researcher.yaml").exists()
    assert (tmp_path / "coder.yaml").exists()
    assert (tmp_path / "email-drafter.yaml").exists()
    assert (tmp_path / "scheduler.yaml").exists()


def test_does_not_overwrite_existing(tmp_path: Path):
    (tmp_path / "user.yaml").write_text("name: user\nmodel: m\nsystem_prompt: x\n")
    installed = install_seeds_if_empty(tmp_path)
    assert installed == []
    assert (tmp_path / "user.yaml").exists()
    assert not (tmp_path / "web-researcher.yaml").exists()


def test_creates_missing_target_dir(tmp_path: Path):
    target = tmp_path / "nested" / "templates"
    installed = install_seeds_if_empty(target)
    assert len(installed) == 4
    assert target.exists()


def test_seed_templates_load_via_loader(tmp_path: Path):
    """Every seed YAML must pass the strict template loader unchanged."""
    from kc_subagents.templates import load_template_file
    install_seeds_if_empty(tmp_path)
    for stem in ("web-researcher", "coder", "email-drafter", "scheduler"):
        t = load_template_file(tmp_path / f"{stem}.yaml")
        assert t.name == stem
