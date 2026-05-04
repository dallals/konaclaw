# kc-sandbox — Smoke Checklist

Run by hand on the target machine after `pip install -e ../kc-core ".[dev]"`.

## Automated tests

- [ ] In `kc-core`: `pytest tests/ --ignore=tests/live` — all green (incl. 2 new permission_check tests).
- [ ] In `kc-sandbox`: `pytest tests/` — all green.

## End-to-end (Python REPL)

```python
from pathlib import Path
import asyncio, tempfile
from kc_core.ollama_client import OllamaClient
from kc_sandbox.permissions import AlwaysAllow
from kc_sandbox.wiring import build_sandboxed_agent

tmp = Path(tempfile.mkdtemp())
research = tmp / "research"; research.mkdir()
(tmp / "shares.yaml").write_text(f"shares:\n  - name: research\n    path: {research}\n    mode: read-write\n")

agent = build_sandboxed_agent(
    agent_yaml=Path("tests/fixtures/agents/filebot.yaml"),
    shares_yaml=tmp / "shares.yaml",
    undo_db=tmp / "undo.db",
    client=OllamaClient(model="gemma3:4b"),
    approval_callback=AlwaysAllow(),
)

reply = asyncio.run(agent.send(
    "create research/hello.md with the text 'hi from kc-sandbox', then list the share"
))
print(reply.content)
```

- [ ] The model writes the file. `cat <tmp>/research/hello.md` shows the expected content.
- [ ] `git --git-dir=<tmp>/research/.kc-journal log --oneline` shows the init commit + the file.write commit.

## Undo round-trip

- [ ] After the write above, run:

```python
from kc_sandbox.undo import UndoLog, Undoer
from kc_sandbox.journal import Journal

log = UndoLog(tmp / "undo.db")
journals = {"research": Journal(research)}
Undoer(journals=journals, log=log).undo(entry_id=1)
```

- [ ] `<tmp>/research/hello.md` is gone.
- [ ] `git --git-dir=<tmp>/research/.kc-journal log --oneline` shows a new "Revert" commit on top.

## Negative cases

- [ ] An agent call asking for `share="research", relpath="../etc/passwd"` returns a tool error containing `escapes share`, the agent loop continues, no file is touched outside the share.
- [ ] An agent call to `file.write` against a read-only share returns a tool error containing `read-only`.
- [ ] An agent call to `file.delete` with `approval_callback=AlwaysDeny()` results in a "Denied" tool result; no file is removed; no undo entry is created.
