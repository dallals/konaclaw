# kc-supervisor v0.2.1 polish — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Encrypted secrets store (login Keychain + AES-GCM), persistent connector→conversation map, denied-call audit visibility, and launcher hardening. All additive in `kc-supervisor` plus a 10-line edit to `KonaClawDashboard.command`.

**Architecture:** New `SecretsStore` module with a pluggable `KeychainBackend` (real `security` CLI for prod, in-memory fake for tests). Boot path migrates plaintext `secrets.yaml` → `secrets.yaml.enc` once, then loads from ciphertext only. Connector conversation continuity moves from a Python dict to a `connector_conv_map` SQLite table keyed on `(channel, chat_id, agent)`. Denied tool calls write an audit row with `decision="denied"` from inside the existing `make_audit_aware_callback`.

**Tech Stack:** Python 3.11, FastAPI, SQLite via stdlib `sqlite3`, `cryptography` for AES-GCM, macOS `security` CLI for Keychain, pytest + pytest-asyncio for tests.

**Spec:** `docs/superpowers/specs/2026-05-06-kc-supervisor-v021-polish-design.md`

---

## File map

| File | Role |
|---|---|
| `kc-supervisor/src/kc_supervisor/secrets_store.py` | **New.** `SecretsStore`, `KeychainBackend` protocol, `SecurityCliKeychain`, errors. |
| `kc-supervisor/tests/test_secrets_store.py` | **New.** Roundtrip, tamper, migration, double-migration, full-config. |
| `kc-supervisor/src/kc_supervisor/storage.py` | **Modify.** Add `connector_conv_map` table + 3 helpers + extend `list_audit` `decision` param. |
| `kc-supervisor/tests/test_storage.py` | **Modify.** Add tests for new helpers + filter param. |
| `kc-supervisor/src/kc_supervisor/inbound.py` | **Modify.** Drop `_conv_by_chat` dict; use storage helpers. |
| `kc-supervisor/tests/test_inbound.py` | **Modify.** Add restart-persistence test. |
| `kc-supervisor/src/kc_supervisor/audit_tools.py` | **Modify.** Extend `make_audit_aware_callback` to record deny rows. |
| `kc-supervisor/tests/test_audit_tools.py` | **New.** Denied-call recording tests. |
| `kc-supervisor/src/kc_supervisor/main.py` | **Modify.** Use `SecretsStore.load()` instead of reading `secrets.yaml` directly. |
| `kc-supervisor/pyproject.toml` | **Modify.** Add `cryptography>=42` dep. |
| `KonaClawDashboard.command` | **Modify.** Trap `INT TERM HUP EXIT` + lsof port pre-flight. |

---

## Task 1: Add `cryptography` dep + skeleton secrets module

**Files:**
- Modify: `kc-supervisor/pyproject.toml`
- Create: `kc-supervisor/src/kc_supervisor/secrets_store.py`

- [ ] **Step 1: Add `cryptography` dependency**

Edit `kc-supervisor/pyproject.toml`. In the `[project]` `dependencies` list, add `"cryptography>=42"`:

```toml
dependencies = [
    "kc-core",
    "kc-sandbox",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "pydantic>=2.6",
    "pyyaml>=6.0",
    "cryptography>=42",
]
```

- [ ] **Step 2: Install**

Run from `kc-supervisor/`:

```bash
.venv/bin/pip install -e .
```

Expected: `Successfully installed cryptography-XX.X.X` (or "already satisfied").

- [ ] **Step 3: Create empty module with imports + error types**

Write `kc-supervisor/src/kc_supervisor/secrets_store.py`:

```python
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
```

- [ ] **Step 4: Commit**

```bash
git add kc-supervisor/pyproject.toml kc-supervisor/src/kc_supervisor/secrets_store.py
git commit -m "feat(kc-supervisor): add cryptography dep + secrets_store skeleton"
```

---

## Task 2: SecretsStore roundtrip (no migration yet)

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/secrets_store.py`
- Create: `kc-supervisor/tests/test_secrets_store.py`

- [ ] **Step 1: Write the failing test**

Write `kc-supervisor/tests/test_secrets_store.py`:

```python
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
```

- [ ] **Step 2: Run tests; confirm they fail**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_secrets_store.py -v
```

Expected: import error or `AttributeError: SecretsStore`.

- [ ] **Step 3: Implement `SecretsStore` (no migration yet)**

Append to `kc-supervisor/src/kc_supervisor/secrets_store.py`:

```python
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
```

- [ ] **Step 4: Run tests; confirm they pass**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_secrets_store.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/secrets_store.py kc-supervisor/tests/test_secrets_store.py
git commit -m "feat(kc-supervisor): SecretsStore save/load with AES-GCM + injectable keychain"
```

---

## Task 3: Plaintext-to-ciphertext migration

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/secrets_store.py`
- Modify: `kc-supervisor/tests/test_secrets_store.py`

- [ ] **Step 1: Add failing tests**

Append to `kc-supervisor/tests/test_secrets_store.py`:

```python
def test_migration_from_plaintext(tmp_path: Path) -> None:
    plaintext_path = tmp_path / "secrets.yaml"
    plaintext_path.write_text("telegram_bot_token: abc:123\n")
    store = SecretsStore(config_dir=tmp_path, keychain=FakeKeychain())

    out = store.load()

    assert out == {"telegram_bot_token": "abc:123"}
    assert not plaintext_path.exists()
    assert (tmp_path / "secrets.yaml.enc").exists()


def test_double_migration_is_noop(tmp_path: Path) -> None:
    (tmp_path / "secrets.yaml").write_text("k: v\n")
    keychain = FakeKeychain()
    store = SecretsStore(config_dir=tmp_path, keychain=keychain)

    first = store.load()
    enc_bytes_after_first = (tmp_path / "secrets.yaml.enc").read_bytes()
    second = store.load()

    assert first == {"k": "v"}
    assert second == {"k": "v"}
    # Second load must not have re-encrypted (different nonce → different bytes)
    assert (tmp_path / "secrets.yaml.enc").read_bytes() == enc_bytes_after_first


def test_full_config_roundtrip(tmp_path: Path) -> None:
    payload = {
        "telegram_bot_token": "8123:abc",
        "telegram_allowlist": ["@sammydallal"],
        "imessage_allowlist": ["+15551234567"],
        "google_credentials_json_path": "/Users/sammy/google.json",
        "zapier_api_key": "zk_live_xyz",
        "openai_api_key": "sk-xyz",
    }
    store = SecretsStore(config_dir=tmp_path, keychain=FakeKeychain())
    store.save(payload)
    assert store.load() == payload
```

- [ ] **Step 2: Run tests; confirm migration tests fail**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_secrets_store.py -v
```

Expected: 3 new tests fail (migration not implemented). Existing 4 still pass.

- [ ] **Step 3: Implement migration in `load()`**

Replace `SecretsStore.load` in `secrets_store.py`:

```python
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
```

- [ ] **Step 4: Run tests; confirm all pass**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_secrets_store.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/secrets_store.py kc-supervisor/tests/test_secrets_store.py
git commit -m "feat(kc-supervisor): SecretsStore one-shot migration from plaintext secrets.yaml"
```

---

## Task 4: Real macOS Keychain backend

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/secrets_store.py`

- [ ] **Step 1: Implement `SecurityCliKeychain`**

Append to `secrets_store.py`:

```python
class SecurityCliKeychain:
    """Keychain backed by the macOS `security` CLI.

    Uses the user's login keychain (default), so the master key is
    auto-unlocked at user login — no boot-time prompt.
    """

    SERVICE = "com.konaclaw.supervisor"
    ACCOUNT = "secrets-master-key"

    def __init__(self, security_bin: str = "security") -> None:
        self._security_bin = security_bin
        if shutil.which(security_bin) is None:
            raise SecretsStoreError(
                "login Keychain unavailable; KonaClaw needs the macOS `security` "
                "CLI to read secrets"
            )

    def get(self) -> Optional[str]:
        result = subprocess.run(
            [self._security_bin, "find-generic-password", "-s", self.SERVICE,
             "-a", self.ACCOUNT, "-w"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            # Item not found is exit 44; treat all errors as missing.
            return None
        return result.stdout.strip()

    def set(self, value: str) -> None:
        result = subprocess.run(
            [self._security_bin, "add-generic-password", "-U",
             "-s", self.SERVICE, "-a", self.ACCOUNT, "-w", value],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise SecretsStoreError(
                f"`security add-generic-password` failed: {result.stderr.strip()}"
            )
```

- [ ] **Step 2: Smoke-test the real CLI manually**

Run from any shell where the user is logged in:

```bash
python3 -c "
from kc_supervisor.secrets_store import SecurityCliKeychain
k = SecurityCliKeychain()
k.set('test-value-konaclaw-delete-me')
print('got:', k.get())
"
```

Expected: `got: test-value-konaclaw-delete-me`. Then delete:

```bash
security delete-generic-password -s com.konaclaw.supervisor -a secrets-master-key
```

- [ ] **Step 3: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/secrets_store.py
git commit -m "feat(kc-supervisor): SecurityCliKeychain backend over macOS security CLI"
```

---

## Task 5: Wire SecretsStore into supervisor boot

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/main.py`

- [ ] **Step 1: Locate the existing plaintext read**

Run:

```bash
grep -n "secrets.yaml\|safe_load.*config" kc-supervisor/src/kc_supervisor/main.py
```

Note the line(s) that load the plaintext today. Replace those with a `SecretsStore` call.

- [ ] **Step 2: Edit `main.py`**

Add near the top of `main.py`'s imports:

```python
from kc_supervisor.secrets_store import SecretsStore, SecurityCliKeychain
```

Replace the existing plaintext read block. Old (representative pattern):

```python
secrets_path = CONFIG_DIR / "secrets.yaml"
secrets = yaml.safe_load(secrets_path.read_text()) if secrets_path.exists() else {}
```

New:

```python
secrets_store = SecretsStore(config_dir=CONFIG_DIR, keychain=SecurityCliKeychain())
secrets = secrets_store.load()
```

Also expose the store on `Deps` so `connectors_routes.py` (next plan) can `save()`:

```python
deps = Deps(
    ...,
    secrets_store=secrets_store,  # add this field on the Deps dataclass
    ...,
)
```

If `Deps` doesn't yet have a `secrets_store` field, add `secrets_store: Optional[Any] = None` to its dataclass definition (search for `class Deps` or `@dataclass\nclass Deps`).

- [ ] **Step 3: Smoke-test boot end-to-end**

Run from `kc-supervisor/`:

```bash
KC_HOME=/tmp/kc-test-boot .venv/bin/kc-supervisor &
SUP_PID=$!
sleep 2
curl -sf http://127.0.0.1:8765/health
kill $SUP_PID
```

Expected: `{"status":"ok"}` from the health endpoint. If you have an existing `~/KonaClaw/config/secrets.yaml`, the migration log line should appear: `migrated secrets to encrypted store`.

- [ ] **Step 4: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/main.py
git commit -m "feat(kc-supervisor): boot reads secrets via SecretsStore (migrates plaintext on first run)"
```

---

## Task 6: connector_conv_map table + storage helpers

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/storage.py`
- Modify: `kc-supervisor/tests/test_storage.py`

- [ ] **Step 1: Write failing tests**

Append to `kc-supervisor/tests/test_storage.py`:

```python
def test_conv_map_put_and_get(tmp_path):
    s = Storage(tmp_path / "db.sqlite")
    s.init()
    cid = s.create_conversation(agent="kona", channel="telegram")

    s.put_conv_for_chat("telegram", "12345", "kona", cid)

    assert s.get_conv_for_chat("telegram", "12345", "kona") == cid
    assert s.get_conv_for_chat("telegram", "99999", "kona") is None


def test_conv_map_persists_across_reopens(tmp_path):
    db = tmp_path / "db.sqlite"
    s1 = Storage(db); s1.init()
    cid = s1.create_conversation(agent="kona", channel="telegram")
    s1.put_conv_for_chat("telegram", "12345", "kona", cid)

    s2 = Storage(db); s2.init()
    assert s2.get_conv_for_chat("telegram", "12345", "kona") == cid


def test_conv_map_channel_isolation(tmp_path):
    s = Storage(tmp_path / "db.sqlite"); s.init()
    cid_t = s.create_conversation(agent="kona", channel="telegram")
    cid_i = s.create_conversation(agent="kona", channel="imessage")

    s.put_conv_for_chat("telegram", "abc", "kona", cid_t)
    s.put_conv_for_chat("imessage", "abc", "kona", cid_i)

    assert s.get_conv_for_chat("telegram", "abc", "kona") == cid_t
    assert s.get_conv_for_chat("imessage", "abc", "kona") == cid_i


def test_conv_map_cascade_on_conversation_delete(tmp_path):
    s = Storage(tmp_path / "db.sqlite"); s.init()
    cid = s.create_conversation(agent="kona", channel="telegram")
    s.put_conv_for_chat("telegram", "abc", "kona", cid)

    s.delete_conversation(cid)

    assert s.get_conv_for_chat("telegram", "abc", "kona") is None


def test_conv_map_upsert(tmp_path):
    s = Storage(tmp_path / "db.sqlite"); s.init()
    cid1 = s.create_conversation(agent="kona", channel="telegram")
    cid2 = s.create_conversation(agent="kona", channel="telegram")

    s.put_conv_for_chat("telegram", "abc", "kona", cid1)
    s.put_conv_for_chat("telegram", "abc", "kona", cid2)  # overwrite

    assert s.get_conv_for_chat("telegram", "abc", "kona") == cid2
```

- [ ] **Step 2: Run tests; confirm they fail**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_storage.py -v -k conv_map
```

Expected: 5 fails (`AttributeError: 'Storage' object has no attribute 'put_conv_for_chat'`).

- [ ] **Step 3: Add table to SCHEMA**

Find the `SCHEMA` constant in `kc-supervisor/src/kc_supervisor/storage.py` and append a new `CREATE TABLE` block before the closing of the schema string:

```sql
CREATE TABLE IF NOT EXISTS connector_conv_map (
    channel    TEXT NOT NULL,
    chat_id    TEXT NOT NULL,
    agent      TEXT NOT NULL,
    conv_id    INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (channel, chat_id, agent),
    FOREIGN KEY (conv_id) REFERENCES conversations(id) ON DELETE CASCADE
);
```

- [ ] **Step 4: Enable foreign keys + add helpers**

In the same file, in `Storage.connect()` (or wherever the connection is opened), ensure `PRAGMA foreign_keys = ON` is set on each connection. If it's already there, skip. If not, add right after `con = sqlite3.connect(...)`:

```python
con.execute("PRAGMA foreign_keys = ON")
```

Add three new methods to the `Storage` class (place after `delete_conversation` for cohesion):

```python
    # ----- connector → conversation map -----
    def put_conv_for_chat(
        self, channel: str, chat_id: str, agent: str, conv_id: int,
    ) -> None:
        with self.connect() as c:
            c.execute(
                "INSERT INTO connector_conv_map (channel, chat_id, agent, conv_id) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT(channel, chat_id, agent) DO UPDATE SET "
                "conv_id=excluded.conv_id, updated_at=CURRENT_TIMESTAMP",
                (channel, chat_id, agent, conv_id),
            )

    def get_conv_for_chat(
        self, channel: str, chat_id: str, agent: str,
    ) -> Optional[int]:
        with self.connect() as c:
            row = c.execute(
                "SELECT conv_id FROM connector_conv_map "
                "WHERE channel=? AND chat_id=? AND agent=?",
                (channel, chat_id, agent),
            ).fetchone()
        return row["conv_id"] if row else None

    def clear_conv_for_chat(
        self, channel: str, chat_id: str, agent: str,
    ) -> None:
        with self.connect() as c:
            c.execute(
                "DELETE FROM connector_conv_map "
                "WHERE channel=? AND chat_id=? AND agent=?",
                (channel, chat_id, agent),
            )
```

- [ ] **Step 5: Run tests; confirm all pass**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_storage.py -v -k conv_map
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/storage.py kc-supervisor/tests/test_storage.py
git commit -m "feat(kc-supervisor): connector_conv_map table + put/get/clear helpers"
```

---

## Task 7: Switch InboundRouter from in-memory dict to storage

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/inbound.py`
- Modify: `kc-supervisor/tests/test_inbound.py`

- [ ] **Step 1: Read existing InboundRouter to understand the call site**

Run:

```bash
grep -n "_conv_by_chat\|create_conversation" kc-supervisor/src/kc_supervisor/inbound.py
```

Identify the lookup-or-create block currently using `self._conv_by_chat`.

- [ ] **Step 2: Write a failing persistence test**

Append to `kc-supervisor/tests/test_inbound.py` (mirror the existing test setup; if a fixture for `InboundRouter` already exists, reuse it):

```python
@pytest.mark.asyncio
async def test_inbound_router_conversation_persists_across_reconstruction(
    tmp_path, monkeypatch,
):
    """A reconstructed InboundRouter (simulated supervisor restart) reuses
    the same conversation_id for the same (channel, chat_id, agent) tuple."""
    # Use the existing test harness for InboundRouter — see the file's
    # earlier tests for the exact fixture wiring (storage + registry +
    # mock send_stream). The behavioral assertion is:
    #
    #   router1.handle(envelope_from_alice)  -> creates conv 5
    #   router2 = InboundRouter(same storage, same registry)  # restart
    #   router2.handle(envelope_from_alice)  -> reuses conv 5

    # ... use the fixture pattern from test_inbound_router_basic in this file.
    # The assertion below is the only new piece.
    storage = ...  # from existing fixture
    cid_first = storage.get_conv_for_chat("telegram", "alice", "kona")
    assert cid_first is not None

    # Reconstruct router with same storage; verify same cid is reused.
    # (No new conversation rows created.)
    rows_before = len(storage.list_conversations(agent="kona"))
    # ... router2.handle(same envelope) ...
    rows_after = len(storage.list_conversations(agent="kona"))
    assert rows_after == rows_before
```

> **Note on the fixture:** copy the existing `test_inbound_router_*` fixture wiring rather than reinventing — the file has a working pattern that mocks `registry`, `send_stream`, and the message envelope. The new test only adds the persistence assertion at the end.

- [ ] **Step 3: Run tests; confirm failure**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_inbound.py -v
```

Expected: new test fails (or errors on missing `get_conv_for_chat` calls because the router still uses the dict).

- [ ] **Step 4: Replace `_conv_by_chat` with storage calls**

In `kc-supervisor/src/kc_supervisor/inbound.py`:

a) **Remove** the line `self._conv_by_chat: dict[tuple[str, str], int] = {}` from `InboundRouter.__init__`.

b) **Replace** the existing lookup-or-create block. The code currently looks roughly like:

```python
key = (envelope.channel, envelope.chat_id)
cid = self._conv_by_chat.get(key)
if cid is None:
    cid = self.storage.create_conversation(agent=agent_name, channel=envelope.channel)
    self._conv_by_chat[key] = cid
```

Replace with:

```python
cid = self.storage.get_conv_for_chat(envelope.channel, envelope.chat_id, agent_name)
if cid is None or self.storage.get_conversation(cid) is None:
    cid = self.storage.create_conversation(agent=agent_name, channel=envelope.channel)
    self.storage.set_conversation_title(cid, f"{envelope.channel}:{envelope.chat_id}")
    self.storage.put_conv_for_chat(envelope.channel, envelope.chat_id, agent_name, cid)
```

- [ ] **Step 5: Run tests; confirm all pass**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_inbound.py -v
```

Expected: all green (existing tests continue to pass; new persistence test passes).

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/inbound.py kc-supervisor/tests/test_inbound.py
git commit -m "feat(kc-supervisor): InboundRouter uses connector_conv_map (survives restart)"
```

---

## Task 8: Denied-call audit rows

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/audit_tools.py`
- Create: `kc-supervisor/tests/test_audit_tools.py`

- [ ] **Step 1: Write failing tests**

Write `kc-supervisor/tests/test_audit_tools.py`:

```python
from __future__ import annotations
import json
from pathlib import Path

import pytest

from kc_sandbox.permissions import Decision, PermissionEngine, Tier
from kc_supervisor.audit_tools import make_audit_aware_callback
from kc_supervisor.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "db.sqlite")
    s.init()
    return s


@pytest.fixture
def engine_denying() -> PermissionEngine:
    return PermissionEngine(
        tier_map={"dangerous_tool": Tier.DENIED},
        agent_overrides={},
        approval_callback=lambda *_a, **_kw: (False, "policy"),
    )


@pytest.fixture
def engine_allowing() -> PermissionEngine:
    return PermissionEngine(
        tier_map={"safe_tool": Tier.SAFE},
        agent_overrides={},
        approval_callback=lambda *_a, **_kw: (True, None),
    )


@pytest.mark.asyncio
async def test_denied_tier_writes_audit_row(storage, engine_denying) -> None:
    cb = make_audit_aware_callback(
        engine_denying, agent_name="kona", storage=storage,
    )
    allowed, reason = await cb("kona", "dangerous_tool", {"x": 1})

    assert allowed is False
    rows = storage.list_audit(agent="kona")
    assert len(rows) == 1
    assert rows[0]["tool"] == "dangerous_tool"
    assert rows[0]["decision"] == "denied"
    parsed = json.loads(rows[0]["result"])
    assert "reason" in parsed and "source" in parsed
    assert rows[0]["undoable"] == 0


@pytest.mark.asyncio
async def test_user_rejected_approval_writes_audit_row(storage) -> None:
    engine = PermissionEngine(
        tier_map={"sketchy_tool": Tier.DESTRUCTIVE},
        agent_overrides={},
        approval_callback=lambda *_a, **_kw: (False, "user said no"),
    )
    cb = make_audit_aware_callback(engine, agent_name="kona", storage=storage)
    allowed, _ = await cb("kona", "sketchy_tool", {})

    assert allowed is False
    rows = storage.list_audit(agent="kona")
    assert len(rows) == 1
    assert rows[0]["decision"] == "denied"


@pytest.mark.asyncio
async def test_allowed_call_does_not_write_denied_row(storage, engine_allowing) -> None:
    cb = make_audit_aware_callback(
        engine_allowing, agent_name="kona", storage=storage,
    )
    allowed, _ = await cb("kona", "safe_tool", {})

    # Allowed → tool runs, AuditingToolRegistry writes its own row later.
    # The callback itself MUST NOT write a duplicate row.
    assert allowed is True
    assert storage.list_audit(agent="kona") == []
```

- [ ] **Step 2: Run tests; confirm failure**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_audit_tools.py -v
```

Expected: `TypeError: make_audit_aware_callback() got an unexpected keyword argument 'storage'`.

- [ ] **Step 3: Update `make_audit_aware_callback`**

Edit `kc-supervisor/src/kc_supervisor/audit_tools.py`. Replace the function (currently at line 113) with:

```python
def make_audit_aware_callback(
    engine: PermissionEngine, *, agent_name: str, storage: Optional[Storage] = None,
):
    """Return an async permission_check that calls engine.check_async, stashes
    the resulting Decision in _decision_contextvar, and writes an audit row
    when the decision is a deny.

    Storage is optional only because some test setups don't need a DB; in
    production the supervisor always passes it.

    Replaces engine.to_async_agent_callback when wiring an AssembledAgent.
    """

    async def _check(_runtime_agent_name: str, tool: str, args: dict[str, Any]) -> tuple[bool, Optional[str]]:
        d = await engine.check_async(agent=agent_name, tool=tool, arguments=args)
        _decision_contextvar.set(d)
        if not d.allowed and storage is not None:
            storage.append_audit(
                agent=agent_name,
                tool=tool,
                args_json=json.dumps(args, default=str),
                decision="denied",
                result=json.dumps({"reason": d.reason, "source": d.source}),
                undoable=False,
            )
        return (d.allowed, d.reason)

    return _check
```

- [ ] **Step 4: Update assembly.py to pass `storage`**

Run:

```bash
grep -n "make_audit_aware_callback" kc-supervisor/src/kc_supervisor/assembly.py
```

At each call site, add `storage=audit_storage` (or whatever variable holds the supervisor's `Storage` instance — search the surrounding lines for the existing `audit_storage=...` argument to `AuditingToolRegistry`):

Old:

```python
make_audit_aware_callback(engine, agent_name=cfg.name)
```

New:

```python
make_audit_aware_callback(engine, agent_name=cfg.name, storage=audit_storage)
```

- [ ] **Step 5: Run tests; confirm all pass**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_audit_tools.py tests/test_assembly.py -v
```

Expected: green. If `test_assembly.py` breaks, the call-site change in step 4 needs `storage` plumbed through the test fixture too — search for `make_audit_aware_callback` in `tests/test_assembly.py` and apply the same edit.

- [ ] **Step 6: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/audit_tools.py kc-supervisor/src/kc_supervisor/assembly.py kc-supervisor/tests/test_audit_tools.py kc-supervisor/tests/test_assembly.py
git commit -m "feat(kc-supervisor): denied tool calls write audit rows with reason+source"
```

---

## Task 9: Extend list_audit with `decision` filter param (consumed by dashboard spec)

**Files:**
- Modify: `kc-supervisor/src/kc_supervisor/storage.py`
- Modify: `kc-supervisor/src/kc_supervisor/http_routes.py`
- Modify: `kc-supervisor/tests/test_storage.py`
- Modify: `kc-supervisor/tests/test_http.py`

- [ ] **Step 1: Write failing storage test**

Append to `kc-supervisor/tests/test_storage.py`:

```python
def test_list_audit_decision_filter(tmp_path):
    s = Storage(tmp_path / "db.sqlite"); s.init()
    s.append_audit(agent="kona", tool="t1", args_json="{}", decision="allowed", result="ok", undoable=False)
    s.append_audit(agent="kona", tool="t2", args_json="{}", decision="denied", result="reason", undoable=False)
    s.append_audit(agent="kona", tool="t3", args_json="{}", decision="allowed", result="ok", undoable=False)

    assert len(s.list_audit(agent="kona")) == 3
    assert len(s.list_audit(agent="kona", decision="allowed")) == 2
    assert len(s.list_audit(agent="kona", decision="denied")) == 1
```

- [ ] **Step 2: Run tests; confirm failure**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_storage.py::test_list_audit_decision_filter -v
```

Expected: `TypeError: list_audit() got an unexpected keyword argument 'decision'`.

- [ ] **Step 3: Extend `Storage.list_audit`**

Edit `storage.py`. Replace the `list_audit` body:

```python
    def list_audit(
        self, agent: Optional[str] = None, limit: int = 100,
        decision: Optional[str] = None,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if agent is not None:
            clauses.append("agent=?"); params.append(agent)
        if decision is not None:
            clauses.append("decision=?"); params.append(decision)
        sql = "SELECT * FROM audit"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]
```

- [ ] **Step 4: Run storage test; confirm pass**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_storage.py::test_list_audit_decision_filter -v
```

Expected: PASS.

- [ ] **Step 5: Add HTTP test**

Append to `kc-supervisor/tests/test_http.py` (use the existing `client` fixture pattern — copy from a nearby `test_audit_*` test):

```python
def test_audit_endpoint_decision_filter(client, storage):
    storage.append_audit(agent="kona", tool="t1", args_json="{}", decision="allowed", result="ok", undoable=False)
    storage.append_audit(agent="kona", tool="t2", args_json="{}", decision="denied", result="r", undoable=False)

    all_rows = client.get("/audit").json()["entries"]
    only_denied = client.get("/audit?decision=denied").json()["entries"]

    assert len(all_rows) == 2
    assert len(only_denied) == 1
    assert only_denied[0]["decision"] == "denied"
```

- [ ] **Step 6: Extend the HTTP route**

In `kc-supervisor/src/kc_supervisor/http_routes.py`, replace the `list_audit` route signature (around line 198):

Old:

```python
    @app.get("/audit")
    def list_audit(
        agent: Optional[str] = None,
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        rows = app.state.deps.storage.list_audit(agent=agent, limit=limit)
        return {"entries": rows}
```

New:

```python
    @app.get("/audit")
    def list_audit(
        agent: Optional[str] = None,
        limit: int = Query(default=100, ge=1, le=1000),
        decision: Optional[str] = Query(default=None, regex="^(allowed|denied)$"),
    ):
        rows = app.state.deps.storage.list_audit(
            agent=agent, limit=limit, decision=decision,
        )
        return {"entries": rows}
```

- [ ] **Step 7: Run tests; confirm pass**

```bash
cd kc-supervisor && .venv/bin/pytest tests/test_http.py -v -k audit
```

Expected: green.

- [ ] **Step 8: Commit**

```bash
git add kc-supervisor/src/kc_supervisor/storage.py kc-supervisor/src/kc_supervisor/http_routes.py kc-supervisor/tests/test_storage.py kc-supervisor/tests/test_http.py
git commit -m "feat(kc-supervisor): GET /audit accepts ?decision=allowed|denied filter"
```

---

## Task 10: Launcher hardening

**Files:**
- Modify: `KonaClawDashboard.command`

- [ ] **Step 1: Replace the trap line**

Find `KonaClawDashboard.command` line containing `trap '...' INT TERM` and replace with:

```sh
trap 'echo; echo "Shutting down..."; kill "$VITE_PID" "$SUP_PID" 2>/dev/null; wait 2>/dev/null' INT TERM HUP EXIT
```

- [ ] **Step 2: Add port pre-flight before the supervisor and vite launches**

Insert after the `mkdir -p "$KC_HOME_DIR/agents" ...` line, before the ollama curl check:

```sh
for port in 8765 5173; do
    if lsof -nP -iTCP:$port -sTCP:LISTEN >/dev/null 2>&1; then
        echo "Port $port is already in use. Run 'lsof -nP -iTCP:$port' to find the holder."
        exit 1
    fi
done
```

- [ ] **Step 3: Smoke-test the orphan-free path**

```bash
chmod +x KonaClawDashboard.command
./KonaClawDashboard.command &
LAUNCHER_PID=$!
sleep 5
# Simulate window-close: kill the launcher script, NOT Ctrl-C.
kill -HUP $LAUNCHER_PID
sleep 2
# Both children should have exited.
lsof -nP -iTCP:8765 -sTCP:LISTEN
lsof -nP -iTCP:5173 -sTCP:LISTEN
```

Expected: both `lsof` lines print nothing. If either prints a process, the trap didn't catch HUP — recheck step 1.

- [ ] **Step 4: Smoke-test the port pre-flight**

```bash
# Hold port 8765 hostage in another terminal:  python3 -m http.server 8765
./KonaClawDashboard.command
```

Expected: prints `Port 8765 is already in use. Run 'lsof -nP -iTCP:8765' to find the holder.` and exits 1 immediately. No supervisor process spawned.

- [ ] **Step 5: Commit**

```bash
git add KonaClawDashboard.command
git commit -m "feat(launcher): trap HUP/EXIT + lsof port pre-flight to prevent orphans"
```

---

## Task 11: Run full test suite + manual smoke

- [ ] **Step 1: Full pytest**

```bash
cd kc-supervisor && .venv/bin/pytest -q
```

Expected: 143 (existing) + 12 (new) ≈ 155 passed. If something red, fix before declaring done.

- [ ] **Step 2: Update `kc-supervisor/SMOKE.md`**

Append a new section:

```markdown
## v0.2.1 polish smoke

1. Boot with existing plaintext `~/KonaClaw/config/secrets.yaml`.
   - Expect log line `migrated secrets to encrypted store`.
   - Expect `~/KonaClaw/config/secrets.yaml.enc` exists.
   - Expect `~/KonaClaw/config/secrets.yaml` is gone.
   - Restart; expect normal boot (no migration log).

2. Send a Telegram message from an allowlisted chat.
   - Expect a row in `connector_conv_map` (sqlite3 ~/KonaClaw/data/db.sqlite "SELECT * FROM connector_conv_map").
   - Restart supervisor.
   - Send another message from same chat.
   - Expect same conv_id reused (no new row in conversations).

3. Trigger an approval-required tool; click Deny in the dashboard.
   - Expect a row in audit with decision='denied' and result containing the reason.

4. Close the launcher's terminal window without Ctrl-C.
   - Relaunch.
   - Expect clean boot, no "address already in use" errors.
   - If port-in-use: expect a clear error message + immediate exit.
```

- [ ] **Step 3: Commit smoke doc**

```bash
git add kc-supervisor/SMOKE.md
git commit -m "docs(kc-supervisor): SMOKE.md additions for v0.2.1 polish"
```

---

## Done criteria

- All tests in `kc-supervisor/.venv/bin/pytest` pass.
- `~/KonaClaw/config/secrets.yaml.enc` is present after boot; plaintext is gone.
- Telegram conversation continuity survives a supervisor restart.
- Denied tool calls appear in `SELECT * FROM audit WHERE decision='denied'`.
- `GET /audit?decision=denied` returns only denied rows.
- Closing the launcher's terminal window does not orphan supervisor or vite.
- Port-in-use at launch produces a clear error and immediate exit, not a silent half-boot.
