from __future__ import annotations
from pathlib import Path
from typing import Optional

import pytest

from kc_supervisor.secrets_store import (
    DecryptError,
    KeyMissingError,
    SecretsStore,
)


class FakeKeychain:
    def __init__(self, value: Optional[str] = None) -> None:
        self._value = value

    def get(self) -> Optional[str]:
        return self._value

    def set(self, value: str) -> None:
        self._value = value


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    keychain = FakeKeychain()
    store = SecretsStore(config_dir=tmp_path, keychain=keychain)

    store.save({"telegram_bot_token": "abc:123", "imessage_allowlist": ["+15551234567"]})
    out = store.load()

    assert out == {"telegram_bot_token": "abc:123", "imessage_allowlist": ["+15551234567"]}
    assert (tmp_path / "secrets.yaml.enc").exists()
    assert not (tmp_path / "secrets.yaml").exists()


def test_load_with_no_files_returns_empty(tmp_path: Path) -> None:
    store = SecretsStore(config_dir=tmp_path, keychain=FakeKeychain())
    assert store.load() == {}


def test_tamper_detection(tmp_path: Path) -> None:
    keychain = FakeKeychain()
    store = SecretsStore(config_dir=tmp_path, keychain=keychain)
    store.save({"k": "v"})

    enc = tmp_path / "secrets.yaml.enc"
    raw = bytearray(enc.read_bytes())
    raw[-1] ^= 0xFF
    enc.write_bytes(bytes(raw))

    with pytest.raises(DecryptError):
        store.load()


def test_key_missing_after_save(tmp_path: Path) -> None:
    keychain = FakeKeychain()
    store = SecretsStore(config_dir=tmp_path, keychain=keychain)
    store.save({"k": "v"})

    # Wipe the key as if the user removed it from Keychain
    keychain._value = None

    with pytest.raises(KeyMissingError):
        store.load()
