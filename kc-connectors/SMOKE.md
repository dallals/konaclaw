# kc-connectors — Smoke Checklist

## Prereqs

- `~/KonaClaw/config/secrets.yaml` populated (gitignored, outside repo):

  ```yaml
  telegram_bot_token: "1234:abcdef"
  telegram_allowlist: ["YOUR_TELEGRAM_CHAT_ID"]
  imessage_allowlist: ["+15555550100"]    # macOS only
  google_credentials_json_path: "/path/to/credentials.json"
  ```

- `~/KonaClaw/config/routing.yaml` (optional — defaults to `default_agent: kona`):

  ```yaml
  default_agent: kona
  routes:
    telegram:
      "YOUR_TELEGRAM_CHAT_ID": kona
  ```

## Telegram

- [ ] Send a message from your phone to the bot. Supervisor logs the inbound.
      Routed agent replies; you see the reply on your phone.
- [ ] Send from a non-allowlisted chat — the supervisor sees nothing
      (silent drop, by design).

## iMessage (macOS only)

- [ ] System Settings → Privacy & Security → Full Disk Access — grant the
      Python interpreter / supervisor process so it can read `chat.db`.
- [ ] Send an iMessage from an allowlisted handle. Watch the supervisor log
      it; routed agent replies via Messages.app.
- [ ] An iMessage with `is_from_me=1` is silently ignored.

## Gmail / Calendar

- [ ] First run: `kc-supervisor` opens the Google OAuth consent flow in your
      browser (`InstalledAppFlow.run_local_server`). You grant only
      `gmail.modify`, `gmail.send`, `calendar`. Token cached at
      `~/KonaClaw/config/google_token.json`.
- [ ] Through the dashboard, ask kona to "list my next 3 calendar events" —
      `gcal.list_events` runs (SAFE, auto-allowed).
- [ ] Ask "draft an email to me with subject Test and body Hello" —
      `gmail.draft` runs (MUTATING, auto-allowed) and shows the draft id.
- [ ] Ask "send draft <id>" — destructive approval pops in dashboard
      Permissions queue; on approve, the email is sent.

## Negative cases

- [ ] OAuth consent revoked externally
      (https://myaccount.google.com/permissions). Next gmail call fails
      cleanly with a token-error message that the agent surfaces.
- [ ] Telegram bot token revoked → `connector.start` raises during boot
      and the supervisor logs the failure but stays up (no Telegram
      capability that session).

## Known v0.2 follow-ups

- Per-(channel, chat_id) conversation IDs reset on supervisor restart
  (in-memory `_conv_by_chat` map). Persist to SQLite to recover continuity.
- Telegram & iMessage attachment download into auto-created inbox shares.
- Encrypted secrets store.
- Dashboard "Connect Google" button (today, OAuth runs in supervisor terminal).
- Telegram reactions, edit-in-place "thinking…" UX.

## News (NewsAPI.org)

Prereq: `newsapi_api_key` set in the supervisor's encrypted secrets store
(via the dashboard's secrets UI or the plaintext fallback).

- [ ] In Chat, ask Kona: "find me three news stories about AI"
      → Expect three numbered headlines with source + URL.
- [ ] Ask: "what is BBC reporting today?" (Kona should infer source slug
      `bbc-news`).
- [ ] Ask: "show me news from gibberish-publication" → Kona surfaces the
      "(unknown source: …)" message and self-corrects on the next turn.
