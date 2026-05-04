import os
import httpx
import pytest


def _ollama_reachable(url: str) -> bool:
    try:
        r = httpx.get(f"{url}/api/tags", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="session")
def live_ollama_url() -> str:
    url = os.environ.get("KC_LIVE_OLLAMA_URL", "http://localhost:11434")
    if not _ollama_reachable(url):
        pytest.skip(f"Ollama not reachable at {url}; skipping live tests")
    return url


@pytest.fixture(scope="session")
def live_model() -> str:
    return os.environ.get("KC_LIVE_MODEL", "gemma3:4b")
