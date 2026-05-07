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
