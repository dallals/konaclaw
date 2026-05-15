import httpx
import pytest

from kc_attachments.capability import VisionCapabilityCache


def _client(handler):
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


def test_cache_detects_vision_capable_model():
    def handler(req):
        return httpx.Response(200, json={"capabilities": ["completion", "vision", "tools"]})
    cache = VisionCapabilityCache(http=_client(handler), base_url="http://x")
    assert cache.supports_vision("qwen3.6:35b") is True


def test_cache_detects_text_only_model():
    def handler(req):
        return httpx.Response(200, json={"capabilities": ["completion", "tools"]})
    cache = VisionCapabilityCache(http=_client(handler), base_url="http://x")
    assert cache.supports_vision("gemma4:31b") is False


def test_cache_treats_missing_capability_array_as_false():
    def handler(req):
        return httpx.Response(200, json={"details": {"family": "gemma"}})
    cache = VisionCapabilityCache(http=_client(handler), base_url="http://x")
    assert cache.supports_vision("unknown:1b") is False


def test_cache_caches_result_after_first_lookup():
    calls = {"n": 0}
    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"capabilities": ["vision"]})
    cache = VisionCapabilityCache(http=_client(handler), base_url="http://x")
    cache.supports_vision("m")
    cache.supports_vision("m")
    assert calls["n"] == 1


def test_cache_returns_false_on_http_error():
    def handler(req):
        return httpx.Response(500, text="boom")
    cache = VisionCapabilityCache(http=_client(handler), base_url="http://x")
    assert cache.supports_vision("m") is False


def test_cache_returns_false_on_network_error():
    def handler(req):
        raise httpx.ConnectError("dns", request=req)
    cache = VisionCapabilityCache(http=_client(handler), base_url="http://x")
    assert cache.supports_vision("m") is False
