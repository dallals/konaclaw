# kc-core

KonaClaw core agent runtime — sub-project 1 of 8. See the umbrella spec at [`../docs/superpowers/specs/2026-05-02-konaclaw-design.md`](../docs/superpowers/specs/2026-05-02-konaclaw-design.md) for the full architecture.

## What's here

A Python library + CLI that runs a ReAct-style agent loop against an OpenAI-compatible chat-completions endpoint. Defaults to local [Ollama](https://ollama.com), and the same client can also talk to OpenRouter (or any other OpenAI-compatible endpoint) when you supply an API key.

No sandbox, no connectors, no dashboard yet — those live in later sub-projects.

## Install

    python3 -m venv .venv          # requires Python >= 3.11
    source .venv/bin/activate
    pip install -e ".[dev]"

## Use (Ollama, default)

Make sure Ollama is running and you have a model pulled:

    ollama pull gemma3:4b
    # or any other chat-capable model; pass via --default-model.

Then:

    kc-chat --agent tests/fixtures/agents/echo.yaml

Quit with Ctrl-C.

## Use (OpenRouter, fallback)

If you want to talk to a hosted model instead of local Ollama:

    export OPENROUTER_API_KEY=sk-or-...
    kc-chat \
        --agent tests/fixtures/agents/echo.yaml \
        --ollama-url https://openrouter.ai/api/v1 \
        --default-model qwen/qwen-2.5-72b-instruct

The CLI banner shows whether you're connected to Ollama or OpenRouter.

## Test

    pytest tests/ --ignore=tests/live          # fast, no Ollama needed
    pytest tests/live/                          # requires real Ollama

See [`SMOKE.md`](SMOKE.md) for the manual verification checklist.

## Layout

    src/kc_core/
      messages.py          # message dataclasses (User/Assistant/ToolCall/ToolResult)
      tools.py             # Tool + ToolRegistry
      ollama_client.py     # /v1/chat/completions wrapper (sync + streaming)
      tool_call_parser.py  # JSON-in-text tool-call fallback
      agent.py             # Agent class + ReAct loop
      config.py            # AgentConfig YAML loader
      cli.py               # `kc-chat` Typer command
    tests/
      conftest.py          # FakeOllamaClient fixture
      test_*.py            # unit tests (no network)
      fixtures/agents/     # example agent YAML
      live/                # tests that require a running Ollama (auto-skip otherwise)
