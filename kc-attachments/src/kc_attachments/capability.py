from __future__ import annotations

import httpx


class VisionCapabilityCache:
    """Caches per-model `supports_vision` results from Ollama's /api/show.

    Treats every error (network, HTTP, JSON, missing capabilities array) as
    "no vision" — safe default that degrades to OCR-only.
    """

    def __init__(
        self,
        *,
        http: httpx.Client | None = None,
        base_url: str = "http://127.0.0.1:11434",
    ) -> None:
        self._http = http or httpx.Client(timeout=5.0)
        self._base_url = base_url.rstrip("/")
        self._cache: dict[str, bool] = {}

    def supports_vision(self, model: str) -> bool:
        if model in self._cache:
            return self._cache[model]
        result = self._probe(model)
        self._cache[model] = result
        return result

    def _probe(self, model: str) -> bool:
        try:
            resp = self._http.post(
                f"{self._base_url}/api/show",
                json={"model": model},
            )
        except httpx.HTTPError:
            return False
        if resp.status_code >= 400:
            return False
        try:
            data = resp.json()
        except (ValueError,):
            return False
        caps = data.get("capabilities")
        if not isinstance(caps, list):
            return False
        return "vision" in caps
