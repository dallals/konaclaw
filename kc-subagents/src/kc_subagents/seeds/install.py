from __future__ import annotations
import shutil
from pathlib import Path

SEED_DIR = Path(__file__).parent


def install_seeds_if_empty(target_dir: Path) -> list[str]:
    """If target_dir is empty (or missing), copy seed YAMLs into it.

    Returns the list of seed names installed. Existing user files are never
    overwritten — if any *.yaml file is already present, no seeds are installed.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    if any(target_dir.glob("*.yaml")):
        return []
    installed: list[str] = []
    for src in sorted(SEED_DIR.glob("*.yaml")):
        shutil.copy(src, target_dir / src.name)
        installed.append(src.stem)
    return installed
