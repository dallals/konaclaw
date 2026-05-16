# ZeroClaw Port Milestone Design

**Goal:** Port three features from Sammy's ZeroClaw (Raspberry Pi) project into KonaClaw: the money-manager scripts, a dashboard portfolio widget, and Tessy (a Tesla-specialist subagent).

**Status:** Brainstorm complete 2026-05-15. Awaiting per-sub-phase plans.

**Scope:** Three independent sub-phases shipped as one milestone. Each gets its own implementation plan.

**Out of scope for the milestone:**
- Modifying anything on ZeroClaw (read-only access via SSH; one-way copy)
- A "daily digest" framework (originally considered, then narrowed to just the portfolio widget per Sammy)
- Telegram delivery of any of these features (KonaClaw dashboard surface only)
- Cal / weather / news / reminders digest sections from ZeroClaw (KonaClaw already covers these via existing tools/chat)
- Re-syncing from ZeroClaw on an ongoing basis (manual `scp` if/when Sammy updates ZeroClaw)

---

## Shared infrastructure

**Workspace directory:** `SammyClaw/workspace/` at the repo root. Contains the ported scripts plus runtime data. Subdirectory layout:

```
workspace/
├── portfolio.py          # money-manager (sub-phase 1)
├── ytd.py
├── stock.py
├── monte_carlo.py
├── finances.md           # gitignored (private)
├── tesla_price.py        # tessy (sub-phase 3)
├── update_tesla_pricing.py
├── update_tesla_offers.py
├── update_tesla_from_screenshot.py
├── tesla_pricing.json    # gitignored (runtime data)
├── tesla_offers.json     # gitignored (runtime data)
├── tesla_offers.md       # gitignored (runtime data)
└── tests/
    ├── test_smoke.py     # portfolio smoke
    └── test_tesla.py     # tesla unit tests
```

**`.gitignore` entries** (root `.gitignore`):
```
workspace/finances.md
workspace/tesla_pricing.json
workspace/tesla_offers.json
workspace/tesla_offers.md
workspace/*.json.bak.*
```

**Sanitization pattern (applies to every script we copy):**
- Strip the hardcoded `BOT_TOKEN` and `CHAT_ID` constants. KonaClaw delivers via dashboard or chat, not direct Telegram from the script.
- Replace any other hardcoded API keys (`FIRECRAWL_API_KEY`, `GEMINI_API_KEY`) with `os.environ.get("...")` reads. KonaClaw's encrypted secrets store at `~/KonaClaw/config/secrets.yaml.enc` already carries `firecrawl_api_key`; `gemini_api_key` needs to be added before sub-phase 3 ships.
- Delete the Telegram-send code paths (`send_telegram()`, the non-`--silent` output branches). Keep only the `--silent` JSON-emitting paths.

**Execution surface:** All ported Python scripts are invoked by Kona via the existing `terminal_run` tool (Phase A, gated by `KC_TERMINAL_ENABLED`). No new tool is required for execution. Tessy's three tools (sub-phase 3) are thin Python wrappers that shell out to the workspace scripts and parse their JSON output.

---

## Sub-phase 1 — money-manager port

**Files copied from ZeroClaw (via `scp` / `ssh cat`):**

| ZeroClaw path | KonaClaw destination |
|---|---|
| `~/.zeroclaw/workspace/portfolio.py` | `workspace/portfolio.py` |
| `~/.zeroclaw/workspace/ytd.py` | `workspace/ytd.py` |
| `~/.zeroclaw/workspace/stock.py` | `workspace/stock.py` |
| `~/.zeroclaw/workspace/monte_carlo.py` | `workspace/monte_carlo.py` |
| `~/.zeroclaw/workspace/finances.md` | `workspace/finances.md` (gitignored) |
| `~/.zeroclaw/workspace/skills/money-manager/SKILL.md` | `kc-skills/skills/money-manager/SKILL.md` |

**Sanitization specifics:**
- Each of `portfolio.py`, `ytd.py`, `stock.py`, `monte_carlo.py`:
  - Remove the `BOT_TOKEN` and `CHAT_ID` constants and the `send_telegram(...)` helper.
  - Remove the non-`--silent` print branches.
  - Keep the data structures (`HOLDINGS` dict) intact — that's Sammy's portfolio state and there's no reason to refactor.
- `SKILL.md`: update path references from `/home/sammydallal/.zeroclaw/workspace/` to `<repo-root>/workspace/`. Update the "ALWAYS read finances.md" instructions accordingly.

**Kona invocation contract:**
- All four scripts callable via `terminal_run` with `cwd=workspace/`.
- All four print a single-line JSON object on stdout when invoked with `--silent` (this contract already holds in ZeroClaw's versions per `digest_sections/money.py`).
- Non-zero exit code on any failure (network, missing finances.md, etc.).

**Tests:**

`workspace/tests/test_smoke.py`:
```python
import json
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent


def test_portfolio_silent_returns_json():
    """portfolio.py --silent must emit a single JSON object with required keys."""
    r = subprocess.run(
        [sys.executable, "portfolio.py", "--silent"],
        cwd=str(WORKSPACE), capture_output=True, text=True, timeout=15,
    )
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    assert "total_value" in payload
    assert "holdings" in payload
    assert isinstance(payload["holdings"], list)


def test_stock_silent_single_ticker_returns_json():
    r = subprocess.run(
        [sys.executable, "stock.py", "AAPL"],
        cwd=str(WORKSPACE), capture_output=True, text=True, timeout=15,
    )
    # stock.py may not have --silent — if it doesn't, this test should
    # invoke it without --silent and assert the script ran without crashing.
    assert r.returncode == 0, r.stderr
```

We do NOT port ZeroClaw's `digest_sections/test_money.py` — that's tied to their digest framework which we're not building.

**Risks:**
- Yahoo Finance changes its response shape — the scripts assume a specific `query1.finance.yahoo.com/v8/finance/chart/<ticker>` JSON shape. If it changes, the scripts break silently. Sammy has lived with this risk on ZeroClaw; same risk here.
- `finances.md` accidentally committed to git. The `.gitignore` entry must land in the same commit as the file copy.

**Acceptance:**
- Kona can answer "What's my portfolio worth?" by calling `terminal_run` with `python3 portfolio.py --silent` and synthesizing the result.
- The smoke test passes.

---

## Sub-phase 2 — Portfolio dashboard widget

**Depends on:** sub-phase 1 (the scripts must exist at `workspace/portfolio.py`).

### Backend

**New supervisor router:** `kc-supervisor/src/kc_supervisor/portfolio_routes.py` (modeled on `attachments_routes.py`).

**Endpoint:** `GET /portfolio/snapshot?refresh=<bool>`

**Behavior:**
- On call, returns `{cached_at: <iso>, payload: <portfolio_json>, stale: <bool>}` where `payload` is the JSON from `python3 portfolio.py --silent`.
- In-memory cache holds the last successful payload for 60 seconds (configurable via env `KC_PORTFOLIO_CACHE_S`, default `60`).
- `refresh=true` bypasses the cache.
- 5-second subprocess timeout on `portfolio.py`. On timeout / non-zero exit / invalid JSON: return `{error: "...", last_good: <prev_payload_or_null>}` with HTTP 200 (so the widget can render the stale data faded).

**Cache implementation:** simple module-level dict guarded by an `asyncio.Lock`. No persistence — process restart clears it.

**Wiring in main.py:**
```python
from kc_supervisor.portfolio_routes import build_portfolio_router
app.include_router(build_portfolio_router(workspace_dir=repo_root / "workspace"))
```

`repo_root` discovery: derived from `Path(__file__).resolve().parents[N]` until a `pyproject.toml` with `name = "kc-supervisor"` is found, then `.parent`. Alternative: read from `KC_REPO_ROOT` env var with the auto-derive as fallback.

### Frontend

**New API client:** `kc-dashboard/src/api/portfolio.ts`

```typescript
import { getBaseUrl } from "./client";

export interface PortfolioHolding {
  ticker: string;
  value: number;
  day_change: number;
  gain_pct: number;
}

export interface PortfolioSnapshot {
  total_value: number;
  total_gain: number;
  total_day_change: number;
  day_pct: number;
  holdings: PortfolioHolding[];
}

export interface SnapshotResponse {
  cached_at: string;
  payload: PortfolioSnapshot | null;
  stale: boolean;
  error?: string;
  last_good?: PortfolioSnapshot | null;
}

export async function getSnapshot(refresh = false): Promise<SnapshotResponse> {
  const url = `${getBaseUrl()}/portfolio/snapshot${refresh ? "?refresh=true" : ""}`;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`portfolio snapshot failed (${r.status})`);
  return r.json();
}
```

**New widget:** `kc-dashboard/src/components/PortfolioWidget.tsx`

Renders (top to bottom):
1. **Total value** — large display (`$4,524,912`)
2. **Today's change** — `+$76,430 (+1.72%)` colored green/red
3. **Top movers** — top 3 holdings by `abs(day_change)`, each row: ticker, $ change, % change.
4. **Full holdings table** (collapsible) — every holding with value, day change, all-time gain %.
5. **Last updated** — relative time + manual refresh button.

States: loading (skeleton), success, error (with stale `last_good` shown faded if available), empty (`payload === null`).

**Auto-refresh:** every 5 minutes while mounted, using `useEffect` + `setInterval`. Manual refresh button forces immediate `refresh=true`.

**New view:** `kc-dashboard/src/views/Portfolio.tsx` — wraps the widget for the dedicated tab.

**Sidebar entry:** add a "Portfolio" entry to the existing sidebar nav (location depends on how the current dashboard sidebar is structured — adapt to the existing pattern).

### Tests

**Supervisor:** `kc-supervisor/tests/test_portfolio_routes.py`
- Mock `subprocess.run` to return a JSON portfolio payload; assert endpoint returns it.
- Second call within 60s hits cache (no second subprocess call).
- `refresh=true` bypasses cache.
- Subprocess timeout → endpoint returns `{error, last_good}` with the cached payload from the prior successful call.
- Non-zero exit code → same error path.

**Dashboard:** `kc-dashboard/src/components/PortfolioWidget.test.tsx`
- Loading state renders skeleton.
- Success state renders total + day change + 3 movers.
- Error state with `last_good` shows the stale data faded.
- Refresh button click calls API with `refresh=true`.

### Deferred to v2

- Kona narrative on the widget ("Portfolio up 1.2% today, NVDA leading at +3.8%") — would need a separate "/portfolio/narrative" endpoint that asks Kona to interpret the snapshot. Not in v1.
- YTD chart, Monte Carlo results panel — separate widgets in a future expansion.
- Live ticker on the chat sidebar (top movers ticking by) — separate phase.

---

## Sub-phase 3 — Tessy (Tesla subagent)

**Independent of sub-phases 1-2.** Uses the existing subagents infrastructure on the `phase-subagents` branch.

### Files copied from ZeroClaw

| ZeroClaw path | KonaClaw destination | Sanitize? |
|---|---|---|
| `~/.zeroclaw/workspace/tesla_price.py` | `workspace/tesla_price.py` | yes — keys + Telegram |
| `~/.zeroclaw/workspace/update_tesla_pricing.py` | `workspace/update_tesla_pricing.py` | yes — Telegram + pending-confirm UX |
| `~/.zeroclaw/workspace/update_tesla_offers.py` | `workspace/update_tesla_offers.py` | yes — Telegram |
| `~/.zeroclaw/workspace/update_tesla_from_screenshot.py` | `workspace/update_tesla_from_screenshot.py` | **rewritten** — see below |
| `~/.zeroclaw/workspace/tesla_pricing.json` | `workspace/tesla_pricing.json` (gitignored) | no — pure data |
| `~/.zeroclaw/workspace/tesla_offers.json` | `workspace/tesla_offers.json` (gitignored) | no — pure data |
| `~/.zeroclaw/workspace/tesla_offers.md` | `workspace/tesla_offers.md` (gitignored) | no — pure data |

**NOT ported:**
- `rebuild_tessy_prompt.py` — regenerates ZeroClaw's `config.toml` with Tesla offers. Replaced by: Tessy's subagent template loads `tesla_offers.json` at invocation time and injects the current offers into its system prompt programmatically (see "System prompt template" below).
- `describe_image.py` — ZeroClaw's standalone vision helper. Replaced by KonaClaw's existing `read_attachment` tool + the subagent's vision-capable model.

### Sanitization specifics

**`tesla_price.py`:**
- Replace `FIRECRAWL_KEY = os.environ.get("FIRECRAWL_API_KEY", "<hardcoded>")` with `FIRECRAWL_KEY = os.environ.get("FIRECRAWL_API_KEY") or sys.exit("FIRECRAWL_API_KEY not set")`.
- Replace `GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "<hardcoded>")` similarly.
- Remove `BOT_TOKEN` + `CHAT_ID` constants and the `send_telegram(...)` calls. Replace any Telegram report with a `print(json.dumps({...}))` on stdout — Tessy reads stdout and turns it into its tool result.
- Keep `--silent` mode as the only output mode (add `--silent` to all invocation paths if not already universal).

**`update_tesla_pricing.py`:**
- Same key/Telegram sanitization.
- Replace the "post diff to Telegram, reply 'confirm pricing'" flow with: `--nlp <text>` returns the proposed diff as JSON on stdout (Tessy returns this to Kona, who presents it to Sammy); a follow-up `--confirm` call applies it. The pending-file mechanism (15-min expiry, validation) stays exactly as-is in ZeroClaw.

**`update_tesla_offers.py`:** same sanitization.

**`update_tesla_from_screenshot.py` (rewritten):**

Original ZeroClaw flow:
1. Spawn `describe_image.py <image_path>` to OCR the screenshot via Gemini.
2. Parse extracted text.
3. Write `tesla_offers.md`.
4. Spawn `rebuild_tessy_prompt.py` to regenerate ZeroClaw config.

KonaClaw rewrite:
1. Accept an attachment id as the input arg (not a file path).
2. Resolve the attachment id to a file path via `kc_attachments.AttachmentStore.original_path(id)`.
3. Call the Gemini API directly (vision endpoint, base64-encoded image, extraction prompt verbatim from the original `describe_image` invocation).
4. Write `tesla_offers.md` and update `tesla_offers.json`.
5. **No** prompt regeneration — Tessy's template re-reads `tesla_offers.json` at next invocation.

### Subagent template

**Path:** existing subagents template location on `phase-subagents`. Inspect at execution time to find the exact directory and YAML schema. The template file:

```yaml
name: tessy
description: |
  Tesla pricing & offers specialist. Calculates monthly payments, lease deals,
  financing rates, and tracks current Tesla promotions. Can update local
  pricing data via natural language and ingest Tesla offers screenshots.
# Omit `model:` to inherit Kona's KC_DEFAULT_MODEL (currently gemma4:26b-mlx-bf16
# per the model-switch memory). The plan will confirm whether the existing
# subagents YAML schema treats an absent `model:` as inherit-from-default.
system_prompt_template: |
  You are Tessy, KonaClaw's Tesla specialist agent.

  Current pricing data version: {pricing_version}
  Current offers (loaded {offers_loaded_at}):
  {tesla_offers_summary}

  Your tools:
    - tesla.price       — calculate pricing/financing for any Tesla config.
                           Pass either structured params (trim, paint, wheels,
                           interior, zip, months, down, apr) OR a natural-language
                           description via the `nlp` arg.
    - tesla.update_pricing — propose changes to local pricing data (Sammy will
                              confirm). Use this when Sammy says things like
                              "Model Y RWD is now $41,990" or "drop FSD to $7000".
    - tesla.confirm_pricing — apply the last proposed pricing change.
    - tesla.update_offers_from_image — given an attachment id of a Tesla offers
                                        screenshot, extract the offers and update
                                        the local offers database.

  When Sammy asks pricing questions, run tesla.price. When he reports a price
  change, propose via tesla.update_pricing and wait for confirmation before
  calling tesla.confirm_pricing. When he attaches a Tesla offers screenshot,
  call tesla.update_offers_from_image with the attachment id.

  Always cite the data version you used. If pricing feels stale, say so.
tools:
  - tesla.price
  - tesla.update_pricing
  - tesla.confirm_pricing
  - tesla.update_offers_from_image
```

The template's `{tesla_offers_summary}`, `{pricing_version}`, `{offers_loaded_at}` are filled by a small template-render helper that reads the JSON files at template instantiation time.

### New tools (4)

Each is a thin Python tool in `kc-supervisor` (or its own `kc-tessy` module — to be decided in the plan) that shells out via `subprocess.run` and parses JSON output. All tools tier=SAFE for read-only ops, MUTATING for writes.

| Tool | Tier | Args | Behavior |
|---|---|---|---|
| `tesla.price` | SAFE | `nlp: str?, trim: str?, paint: str?, wheels: int?, interior: str?, zip: str?, months: int?, down: number?, apr: number?` | Runs `python3 tesla_price.py --silent <args>` in `workspace/`; returns parsed JSON. |
| `tesla.update_pricing` | MUTATING (approval required) | `nlp: str` | Runs `update_tesla_pricing.py --nlp <text>`; returns proposed diff JSON. |
| `tesla.confirm_pricing` | MUTATING (approval required) | none | Runs `update_tesla_pricing.py --confirm`; returns applied diff. |
| `tesla.update_offers_from_image` | MUTATING (approval required) | `attachment_id: str` | Resolves attachment to path; runs the rewritten screenshot script; returns extracted offers summary. |

### Invocation path

1. Sammy in a Kona chat: *"Ask Tessy about Model Y pricing in 95128, $7k down, 72 months."*
2. Kona calls `delegate_subagent(template="tessy", task="Model Y pricing in 95128, $7k down, 72 months")`.
3. Tessy spawns, loads `tesla_pricing.json` + `tesla_offers.json` into its system prompt.
4. Tessy calls `tesla.price(zip="95128", down=7000, months=72)`.
5. Returns formatted pricing answer to Kona.
6. Kona relays to Sammy.

### Tests

**`workspace/tests/test_tesla.py`:**
- `test_tesla_price_silent_returns_json` — mock urlopen for Firecrawl + Gemini; assert valid JSON output shape.
- `test_update_pricing_validates_paths` — feed a patch that touches a non-existent JSON path; assert script exits non-zero with a "path not in allowlist" message.
- `test_update_pricing_pending_expires` — write a pending file with a 16-min-old timestamp; `--confirm` should refuse.
- `test_update_offers_from_screenshot_attachment_resolution` — mock the attachment store to return a known image path; assert the Gemini extraction prompt + offers.md write.

**`kc-supervisor/tests/test_tessy_subagent.py`:**
- Load the Tessy template; assert the system prompt has the offers summary substituted in.
- Mock the four tools; spawn the subagent with a "Model Y pricing" prompt; assert it calls `tesla.price` with reasonable args.

### Risks

- **`tesla_price.py` fragility:** 55 KB script that scrapes Tesla's site via Firecrawl. Tesla changes their site → the script breaks. Sammy already lives with this on ZeroClaw; we inherit the same risk.
- **Gemini API key:** not currently in KonaClaw secrets. Adding it is small (single `secrets.set` call) but it's a prerequisite for sub-phase 3 to ship.
- **Subagent template substitution:** the existing subagents system on `phase-subagents` may or may not natively support `{placeholder}` substitution in `system_prompt_template`. If not, the template renderer is a small new utility (10-15 lines).
- **Approval prompts for the mutating tools:** `tesla.update_pricing`, `tesla.confirm_pricing`, and `tesla.update_offers_from_image` are MUTATING tier. Sammy will see an approval prompt each time. That's the right default (these write to local data files); if it gets annoying, a follow-up phase can mark them SAFE within Tessy's specific permission scope.

### Deferred to v2

- Tessy delivering pricing via Telegram (kept dashboard-only for milestone consistency).
- A "Tesla" tab in the dashboard showing current pricing/offers (not needed for v1 — Sammy chats with Tessy).
- Auto-detecting price changes by periodically scraping (manual `update_pricing` only in v1).

---

## Milestone-level test summary

| Sub-phase | New tests |
|---|---|
| 1 | 2 smoke tests in `workspace/tests/test_smoke.py` |
| 2 | ~4 supervisor tests + ~4 dashboard component tests |
| 3 | ~4 workspace script tests + ~2 subagent tests |

**Total:** ~16 new tests, no expected regressions in existing suites.

## Milestone-level acceptance

- Kona can answer portfolio questions by shelling out to `portfolio.py`.
- Dashboard has a Portfolio tab showing live snapshot.
- Sammy can invoke Tessy via chat ("ask Tessy...") and Tessy can price configs, propose pricing updates, and ingest Tesla offers screenshots.

---

## Cleanup notes for the implementer

- Don't try to mirror ZeroClaw's `digest.py` orchestration or `digest_sections/` package. That entire mechanism was descoped during brainstorming.
- Don't port `rebuild_tessy_prompt.py` or `describe_image.py` — both replaced (see sub-phase 3).
- Don't commit `finances.md`, `tesla_pricing.json`, `tesla_offers.json`, or `tesla_offers.md`. The `.gitignore` must land in the same commit as the first file copy.
- The `KC_TERMINAL_ENABLED` env flag must be true for Kona to actually run the scripts. Verify this is set in `~/.konaclaw.env` before declaring sub-phase 1 done.
- Tessy needs the Gemini API key in secrets before sub-phase 3 ships. Skill: ask Sammy for it during execution, save via `SecretsStore.save` directly.
