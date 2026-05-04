# kc-core — Smoke Checklist

Run these by hand on the target machine after `pip install -e ".[dev]"`. All must pass before kc-core is considered done.

## Automated tests

- [ ] `pytest tests/ --ignore=tests/live` — all green (no Ollama needed).
- [ ] `pytest tests/live/ -v` (with Ollama running and `gemma3:4b` pulled) — passes; or, in the absence of Ollama, skips with a clear message.

## CLI smoke (Ollama, default)

- [ ] `kc-chat --help` prints usage, listing `--agent`, `--ollama-url`, `--default-model`, `--api-key`, `--stream/--no-stream`.
- [ ] `kc-chat --agent tests/fixtures/agents/echo.yaml` opens an interactive prompt with banner reading "via Ollama".
- [ ] Type "hello" — assistant replies with text streamed token-by-token.
- [ ] Carry on a 3-turn conversation; the assistant remembers prior turns (e.g., "what did I just say?" recalls correctly).
- [ ] Ctrl-C exits cleanly with "bye." printed.

## CLI smoke (OpenRouter, fallback)

You only need this if you want to verify the OpenRouter path. Get a free key at https://openrouter.ai/keys.

- [ ] `OPENROUTER_API_KEY=sk-or-... kc-chat --agent tests/fixtures/agents/echo.yaml --ollama-url https://openrouter.ai/api/v1 --default-model qwen/qwen-2.5-72b-instruct` — banner reads "via OpenRouter".
- [ ] First turn streams a real response from OpenRouter's hosted model.

## Negative cases

- [ ] `kc-chat` (no `--agent`) fails with a clear "missing option" error.
- [ ] `kc-chat --agent /nonexistent.yaml` fails before launching the REPL.
- [ ] `kc-chat --agent tests/fixtures/agents/echo.yaml --ollama-url http://127.0.0.1:1` (a port nothing is listening on) — the first user input shows a clear connection-error message; the REPL stays alive for retry.
