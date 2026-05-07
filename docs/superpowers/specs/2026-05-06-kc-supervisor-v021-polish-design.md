# kc-supervisor v0.2.1 polish — design

**Date:** 2026-05-06
**Status:** draft
**Predecessor:** [2026-05-04-kc-supervisor-v02-wiring-design.md](./2026-05-04-kc-supervisor-v02-wiring-design.md)
**Sibling:** [2026-05-06-kc-dashboard-v021-polish-design.md](./2026-05-06-kc-dashboard-v021-polish-design.md) (UI half)

## Scope

Four backend polish items that were deferred from the v0.2 wiring wave. All additive in `kc-supervisor` plus a 10-line change to `KonaClawDashboard.command`. No changes to kc-core, kc-sandbox, kc-mcp, kc-zapier, kc-connectors, kc-memory, or kc-dashboard.

1. Encrypted secrets store (replaces plaintext `~/KonaClaw/config/secrets.yaml`)
2. Persist connector `_conv_by_chat` to SQLite (channel conversation continuity survives restart)
3. Denied-call audit visibility (record audit rows when permission is denied)
4. Launcher hardening (`trap INT TERM HUP EXIT` + port pre-flight)

Items previously listed in the v0.2 deferred set are already shipped and explicitly *not* in scope here:

- Idempotent `/undo/{audit_id}` — shipped at root commit `4e436a8`
- Memory undo wiring — shipped at `kc_supervisor/http_routes.py:241-242`

## Architecture

### 1. Encrypted secrets store

**Module:** `kc-supervisor/src/kc_supervisor/secrets_store.py` (new).

**Crypto.** AES-256-GCM over the entire YAML payload. 32-byte key, 12-byte random nonce per write. On-disk format: `nonce(12) || ciphertext || tag(16)`. Implementation: `cryptography.hazmat.primitives.ciphers.aead.AESGCM`. Pin `cryptography>=42` in `pyproject.toml`.

**Key storage.** macOS login Keychain. Service `com.konaclaw.supervisor`, account `secrets-master-key`. Read/write via the `security` CLI subprocess — no PyObjC dep. Login Keychain is unlocked at user login, so the launcher boots without prompting.

```
# write
security add-generic-password -U \
  -s com.konaclaw.supervisor -a secrets-master-key -w "$BASE64_KEY"
# read
security find-generic-password \
  -s com.konaclaw.supervisor -a secrets-master-key -w
```

**Files on disk.**

| Path | Role |
|---|---|
| `~/KonaClaw/config/secrets.yaml.enc` | Steady-state ciphertext |
| `~/KonaClaw/config/secrets.yaml` | Migration source only — deleted after first successful encrypt |

**Public API.**

```python
class SecretsStore:
    def __init__(self, config_dir: Path, keychain: KeychainBackend = ...) -> None: ...
    def load(self) -> dict[str, Any]:
        """Return the decrypted secrets dict, running one-shot migration if needed."""
    def save(self, data: dict[str, Any]) -> None:
        """Atomic-write secrets.yaml.enc with a fresh nonce."""
```

`KeychainBackend` is a thin abstraction over the `security` CLI to make tests injectable (an in-memory fake is used in unit tests).

**Boot flow (in `main.py`):**

```
store = SecretsStore(CONFIG_DIR)
secrets = store.load()  # migrates plaintext → ciphertext on first run
deps.secrets = secrets   # existing readers (telegram/imessage/google/zapier) consume this dict
```

**Migration semantics.** Idempotent: `load()` checks `secrets.yaml.enc` first; if present, decrypt and return. If absent but `secrets.yaml` exists, generate-or-fetch the keychain key, encrypt, atomic-write `.enc`, `unlink` the plaintext, log `"migrated secrets to encrypted store"`. If neither file exists, return `{}`. Calling `load()` twice in the same process re-decrypts cleanly.

**Failure modes (boot-fatal).**

- `security` CLI missing or login Keychain locked → exit with `"login Keychain unavailable; KonaClaw needs it to read secrets"`.
- Ciphertext present but key missing in Keychain → `KeyMissingError` with restoration instructions.
- AES-GCM tag mismatch (tampering / wrong key) → `DecryptError`. Do **not** auto-rewrite or wipe.

### 2. Persist connector `_conv_by_chat` to SQLite

**Schema (added to `kc-supervisor/src/kc_supervisor/storage.py:SCHEMA`).**

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

`agent` is part of the primary key so the connector pairing UI (sibling spec) can route the same chat to different agents over time without colliding rows.

**Storage helpers (added to `Storage`).**

```python
def get_conv_for_chat(self, channel: str, chat_id: str, agent: str) -> int | None
def put_conv_for_chat(self, channel: str, chat_id: str, agent: str, conv_id: int) -> None
def clear_conv_for_chat(self, channel: str, chat_id: str, agent: str) -> None
```

`put_conv_for_chat` uses `INSERT INTO ... ON CONFLICT(channel, chat_id, agent) DO UPDATE SET conv_id=excluded.conv_id, updated_at=CURRENT_TIMESTAMP`.

**InboundRouter changes (`kc_supervisor/inbound.py`).** Drop the `_conv_by_chat: dict` field. The lookup-or-create path becomes:

```python
def _get_or_create_conv(self, channel: str, chat_id: str, agent: str) -> int:
    cid = self.storage.get_conv_for_chat(channel, chat_id, agent)
    if cid is not None and self.storage.get_conversation(cid) is not None:
        return cid
    cid = self.storage.create_conversation(agent=agent, channel=channel)
    self.storage.set_conversation_title(cid, f"{channel}:{chat_id}")
    self.storage.put_conv_for_chat(channel, chat_id, agent, cid)
    return cid
```

The `get_conversation()` defensive check guards against manual SQLite edits or future delete-without-cascade paths.

### 3. Denied-call audit visibility

**Problem.** When permission is denied, the agent short-circuits before `tool.invoke`, so the `AuditingToolRegistry` wrapper is never entered. No audit row is written. Dashboard cannot show what the agent attempted.

**Site.** Two paths share one helper. (a) The user-prompt path: wrap `broker.request_approval` (currently called inside `approval_callback` at `assembly.py:231`) so a returned `False` writes the audit row. (b) The synchronous deny path: `PermissionEngine.check_async` returns `Decision(allow=False, source="tier")` without ever invoking `approval_callback` for tier-resolved DENIED tools. Catch this by wrapping the engine's `check_async` (or the kc-core hook that calls it) so any resolved `Decision.allow == False` writes the row. Both sites call the same helper:

```python
deps.storage.write_audit(
    agent=agent_name,
    tool=tool_name,
    args_json=json.dumps(args),
    decision="denied",
    result=json.dumps({"reason": decision.reason, "source": decision.source}),
    undoable=0,
)
```

**No schema migration.** The existing `audit` table has `decision` and `result` columns. We just write `decision="denied"` and stash the reason in `result` as JSON. Existing readers ignore unknown decision values gracefully (the dashboard query in spec 2 will filter on it explicitly).

**Coverage.**

| Source of deny | Recorded? |
|---|---|
| User clicked **Deny** on an approval card | yes |
| Approval timed out / websocket dropped | yes (`source = "approval_timeout"` / `"approval_cancelled"`) |
| Tier-resolved DENIED (no override, no callback) | yes |
| Override callback returned `Decision.allow=False` | yes |
| Hard-coded supervisor rejections (name-regex on `POST /agents`, etc.) | no — not a tool call |

### 4. Launcher hardening

**File.** `KonaClawDashboard.command` (repo root).

**Trap.** Replace `trap 'kill 0' INT TERM` with `trap 'kill 0' INT TERM HUP EXIT`. `HUP` catches terminal-window close (the orphan-process hazard documented in memory); `EXIT` catches every path including `set -e` aborts.

**Port pre-flight.** Before starting supervisor and vite:

```sh
for port in 8765 5173; do
  if lsof -nP -iTCP:$port -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is already in use. Run 'lsof -nP -iTCP:$port' to find the holder."
    exit 1
  fi
done
```

`lsof` is on every macOS by default. No new dependency.

## Data flow

### Boot

```
launcher (.command)
  port pre-flight (8765, 5173) — exit 1 if bound
  start supervisor → main.py
    SecretsStore(CONFIG_DIR).load()
      .enc exists?  decrypt → dict
      else .yaml exists?  encrypt → write .enc → unlink .yaml → dict
      else  {}
    deps.secrets = dict
    existing readers (telegram, imessage, google, zapier) consume deps.secrets
    storage.init_schema()  # connector_conv_map table created idempotently
  start vite
  trap INT TERM HUP EXIT → kill 0
```

### Inbound message (existing flow, modified)

```
Connector receives MessageEnvelope(channel, chat_id, agent, text)
InboundRouter._get_or_create_conv(channel, chat_id, agent)
  storage.get_conv_for_chat(...) → cid or None
  if None or orphan: create_conversation(); put_conv_for_chat(...)
  → cid
proceed with existing pipeline (refresh memory prefix, run send_stream, persist frames)
```

### Tool-call denial

```
agent attempts tool call → PermissionEngine.check → Decision(allow=False, reason, source)
permission_callback receives Decision
  storage.write_audit(decision="denied", result=json.dumps({reason, source}), undoable=0)
agent sees "denied" tool result → continues turn
```

## Error handling

- **Keychain unavailable at boot.** Log structured error, exit 1. Launcher's port pre-flight has already passed, so the user sees the error directly in the terminal.
- **Decrypt fails on existing `.enc`.** `DecryptError` with the path and a hint to delete `.enc` to start fresh. Do not silently rewrite — that would mask key compromise or filesystem corruption.
- **`connector_conv_map` row points at a deleted conversation.** `ON DELETE CASCADE` handles this in the normal path; defensive `get_conversation()` check in `_get_or_create_conv` covers manual edits.
- **`write_audit` for a denied call fails (DB locked, etc.).** Log and continue — denial behavior must not depend on audit success. The agent already saw the deny.

## Testing

All in `kc-supervisor`'s existing pytest suite. ~12 new tests across 3 files.

### `tests/test_secrets_store.py` (~6 tests)

- Roundtrip: `save({"a": 1}) → load() == {"a": 1}` with a fake keychain.
- Tamper detection: flip a byte in `.enc`, `load()` raises `DecryptError`.
- Missing key: `.enc` exists, keychain returns None, `load()` raises `KeyMissingError`.
- Migration path: write plaintext `secrets.yaml`, call `load()`, assert `.enc` exists, `.yaml` is gone, returned dict matches.
- Double-migration is a no-op: call `load()` twice, key is generated once, `.enc` survives both calls.
- Full-config roundtrip: encode every key the existing readers expect (telegram_bot_token, telegram_allowlist, imessage_allowlist, google_credentials_json_path, zapier_api_key, openai_api_key) and assert no data loss.

### `tests/test_connector_persistence.py` (~3 tests)

- Persistence across restart: `put_conv_for_chat`, close + reopen storage, `get_conv_for_chat` returns same id.
- Channel isolation: same `chat_id` under `telegram` and `imessage` keep distinct conv ids.
- FK cascade: delete the conversation row, the map row is gone.

### `tests/test_audit_denied.py` (~3 tests)

- User-rejected approval writes a row with `decision="denied"` and `result` containing the reason.
- Tier=DENIED tool call (no approval ever sent) writes a denied row.
- Allowed call still writes its normal row (regression guard against accidentally writing twice).

### Smoke (manual, in `SMOKE.md`)

- Boot with existing plaintext `secrets.yaml`; observe migration log line; check `secrets.yaml.enc` exists and `secrets.yaml` is gone; restart; observe normal load (no migration).
- Send a Telegram message; observe a new row in `connector_conv_map`; restart supervisor; send another message from the same chat; observe the conversation_id is reused (memory prefix carries forward).
- Trigger a tool that requires approval; click Deny; observe a `decision="denied"` row in the audit table.
- Close the launcher's terminal window without Ctrl-C; relaunch; observe clean boot (no orphan supervisor on `:8765`).

## Out of scope (deferred)

- Encrypted-secrets key rotation tool (manual via `security delete-generic-password` + re-encrypt for now).
- Per-secret reveal/edit endpoint over HTTP — only `secrets_store.load()` is called from server code; no plaintext leaves the process.
- Cross-platform key storage (Linux Secret Service, Windows DPAPI) — macOS-only is fine for v0.2.1; portability is a v2 milestone item.
- Hard-coded supervisor rejections (e.g., name-regex on `POST /agents`) recorded as audit rows — not a tool call, separate concern.

## Cross-references

- [2026-05-02-konaclaw-design.md](./2026-05-02-konaclaw-design.md) — umbrella spec.
- [2026-05-04-kc-supervisor-v02-wiring-design.md](./2026-05-04-kc-supervisor-v02-wiring-design.md) — predecessor wave; this spec extends its "Out of Scope" list.
- [2026-05-06-kc-dashboard-v021-polish-design.md](./2026-05-06-kc-dashboard-v021-polish-design.md) — sibling spec for dashboard UX changes that surface these backend changes (zaps page, connector pairing UI, denied-row pill).
