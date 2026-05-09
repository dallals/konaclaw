# kc-connectors

KonaClaw connectors — sub-project 6 of 8. Provides:

- **Telegram** bot (long-poll + allowlist)
- **iMessage** (chat.db tail + AppleScript send + allowlist) — macOS only at runtime
- **Gmail** tools (search / read / draft / send) via OAuth2
- **Google Calendar** tools (list / create / update / delete) via OAuth2
- **RoutingTable** (per-chat → agent name)
- `secrets.yaml` loader (encrypted store comes in kc-supervisor v0.2)
- **NewsAPI** (`news_adapter.py`) — `news.search_topic` and `news.from_source`
  tools backed by https://newsapi.org/. Free tier: 100 req/day. Configure by
  setting `newsapi_api_key` in the supervisor's encrypted secrets store.
  Both tools are SAFE.

Depends on `kc-core`, `kc-sandbox`, `kc-supervisor`. Tier assignments live
in kc-supervisor's `assemble_agent`: read tools are SAFE, drafts are
MUTATING, sends/creates/updates/deletes are DESTRUCTIVE (require approval).

## Tests

`pytest tests/` — runs on Linux or macOS; iMessage tests use a synthesized
chat.db so they don't require real macOS.
