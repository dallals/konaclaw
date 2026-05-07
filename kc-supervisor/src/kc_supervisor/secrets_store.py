from __future__ import annotations

import base64
import secrets as _secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol

import yaml
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretsStoreError(Exception):
    """Base class for secrets-store errors."""


class KeyMissingError(SecretsStoreError):
    """Ciphertext exists but the master key was not found in the keychain."""


class DecryptError(SecretsStoreError):
    """Ciphertext failed to decrypt (tamper, key mismatch, corruption)."""


class KeychainBackend(Protocol):
    """Abstraction over the macOS `security` CLI so tests can inject a fake."""

    def get(self) -> Optional[str]: ...
    def set(self, value: str) -> None: ...


def _generate_key_b64() -> str:
    return base64.b64encode(_secrets.token_bytes(32)).decode("ascii")


class SecretsStore:
    """Encrypted secrets at <config_dir>/secrets.yaml.enc.

    On-disk format: nonce(12) || ciphertext || tag(16). The master key is a
    base64-encoded 32-byte value held in the injected KeychainBackend.
    """

    ENC_FILENAME = "secrets.yaml.enc"

    def __init__(self, config_dir: Path, keychain: KeychainBackend) -> None:
        self._config_dir = config_dir
        self._keychain = keychain
        self._enc_path = config_dir / self.ENC_FILENAME

    def _get_or_create_key(self) -> bytes:
        key_b64 = self._keychain.get()
        if key_b64 is None:
            key_b64 = _generate_key_b64()
            self._keychain.set(key_b64)
        return base64.b64decode(key_b64)

    def _require_key(self) -> bytes:
        key_b64 = self._keychain.get()
        if key_b64 is None:
            raise KeyMissingError(
                f"master key for {self._enc_path} is missing from the keychain; "
                "restore the keychain entry or delete the .enc file to start fresh"
            )
        return base64.b64decode(key_b64)

    def save(self, data: dict[str, Any]) -> None:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        key = self._get_or_create_key()
        plaintext = yaml.safe_dump(data, sort_keys=True).encode("utf-8")
        nonce = _secrets.token_bytes(12)
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, associated_data=None)
        # Atomic write
        tmp = self._enc_path.with_suffix(".enc.tmp")
        tmp.write_bytes(nonce + ciphertext)
        tmp.replace(self._enc_path)

    def load(self) -> dict[str, Any]:
        if self._enc_path.exists():
            return self._decrypt_file(self._enc_path)
        plaintext_path = self._config_dir / "secrets.yaml"
        if plaintext_path.exists():
            try:
                loaded = yaml.safe_load(plaintext_path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise SecretsStoreError(f"{plaintext_path}: invalid YAML ({exc})") from exc
            data = loaded if isinstance(loaded, dict) else {}
            self.save(data)
            plaintext_path.unlink()
            return data
        return {}

    def _decrypt_file(self, path: Path) -> dict[str, Any]:
        key = self._require_key()
        blob = path.read_bytes()
        if len(blob) < 12 + 16:
            raise DecryptError(f"{path}: ciphertext too short")
        nonce, body = blob[:12], blob[12:]
        try:
            plaintext = AESGCM(key).decrypt(nonce, body, associated_data=None)
        except Exception as exc:  # cryptography raises InvalidTag here
            raise DecryptError(f"{path}: decrypt failed ({type(exc).__name__})") from exc
        loaded = yaml.safe_load(plaintext.decode("utf-8"))
        return loaded if isinstance(loaded, dict) else {}
