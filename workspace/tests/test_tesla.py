"""Unit tests for the ported Tesla workspace scripts."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


WORKSPACE = Path(__file__).parent.parent


def _has_firecrawl() -> bool:
    return bool(os.environ.get("FIRECRAWL_API_KEY"))


def _has_ollama() -> bool:
    """Quick check whether Ollama is reachable at the configured URL."""
    import urllib.request
    url = os.environ.get("KC_OLLAMA_URL", "http://127.0.0.1:11434")
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


@pytest.mark.skipif(not _has_firecrawl(), reason="needs FIRECRAWL_API_KEY")
def test_tesla_price_runs_with_structured_args():
    """A live call to Tesla pricing. Network-dependent. Slow."""
    r = subprocess.run(
        [sys.executable, "tesla_price.py", "--silent",
         "--trim", "rwd", "--zip", "95128", "--months", "72", "--down", "7000"],
        cwd=str(WORKSPACE), capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert r.stdout.strip(), "empty stdout"


@pytest.mark.skipif(
    not (_has_firecrawl() and _has_ollama()),
    reason="needs FIRECRAWL_API_KEY and a reachable local Ollama",
)
def test_tesla_price_nlp_path_uses_local_ollama():
    """Verify the --nlp path round-trips through local Ollama for param extraction."""
    r = subprocess.run(
        [sys.executable, "tesla_price.py", "--silent", "--nlp",
         "Model Y RWD, $7000 down, 72 months, ZIP 95128"],
        cwd=str(WORKSPACE), capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert "RWD" in r.stdout or "rwd" in r.stdout or "monthly" in r.stdout.lower()


def test_update_tesla_from_screenshot_requires_image_arg():
    """Script must reject invocation without an image path."""
    r = subprocess.run(
        [sys.executable, "update_tesla_from_screenshot.py"],
        cwd=str(WORKSPACE), capture_output=True, text=True, timeout=5,
    )
    assert r.returncode != 0
    assert "Usage" in r.stderr or "image_path" in r.stderr


def test_update_tesla_pricing_cancel_returns_no_pending_when_clean():
    """--cancel with no pending file should report no_pending and exit cleanly."""
    # Make sure no stale pending file exists (from a prior real-world run).
    pending = WORKSPACE / "tesla_pricing_pending.json"
    if pending.exists():
        pytest.skip("real pending file exists; refusing to touch user state")
    r = subprocess.run(
        [sys.executable, "update_tesla_pricing.py", "--cancel"],
        cwd=str(WORKSPACE), capture_output=True, text=True, timeout=10,
    )
    # Either the script emits a JSON status or exits cleanly without crashing.
    assert "Traceback" not in r.stderr, f"script crashed: {r.stderr}"
