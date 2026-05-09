from __future__ import annotations
from pathlib import Path
import os
import yaml


def load_secrets() -> dict:
    """Load ~/KonaClaw/config/secrets.yaml (or KC_SECRETS_PATH override).
    NOT encrypted in v1 — encrypted secrets store comes in kc-supervisor v0.2.
    """
    p = Path(os.environ.get("KC_SECRETS_PATH",
                           Path.home() / "KonaClaw" / "config" / "secrets.yaml"))
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}
