import pytest
from kc_web.url_guard import is_public_url


@pytest.mark.parametrize("url", [
    "https://example.com",
    "http://example.com/path?q=1",
    "https://en.wikipedia.org/wiki/Foo",
    "http://8.8.8.8",
    "https://[2001:4860:4860::8888]",
])
def test_public_urls_allowed(url):
    allowed, reason = is_public_url(url)
    assert allowed, f"expected allowed, got reason={reason}"
    assert reason is None


@pytest.mark.parametrize("url,reason", [
    ("http://localhost", "local_hostname"),
    ("http://localhost:3000", "local_hostname"),
    ("http://foo.local", "local_hostname"),
    ("http://bar.internal", "local_hostname"),
    ("http://x.localhost", "local_hostname"),
    ("http://127.0.0.1", "private_ip"),
    ("http://10.0.0.1", "private_ip"),
    ("http://172.16.0.1", "private_ip"),
    ("http://192.168.1.1", "private_ip"),
    ("http://169.254.169.254", "private_ip"),
    ("http://[::1]", "private_ip"),
    ("https://metadata.google.internal", "metadata_endpoint"),
    ("https://metadata", "metadata_endpoint"),
])
def test_local_and_private_blocked(url, reason):
    allowed, got_reason = is_public_url(url)
    assert not allowed
    assert got_reason == reason


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com",
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
])
def test_non_http_schemes_blocked(url):
    allowed, reason = is_public_url(url)
    assert not allowed
    assert reason == "non_http_scheme"


def test_missing_host_blocked():
    allowed, reason = is_public_url("https://")
    assert not allowed
    assert reason == "missing_host"


def test_extra_blocked_hosts_exact_match():
    allowed, reason = is_public_url("https://evil.com", extra_blocked_hosts=["evil.com"])
    assert not allowed
    assert reason == "extra_blocked"


def test_extra_blocked_hosts_no_suffix_match():
    # "evil.com" in blocklist must NOT block "evil.com.allowed.com"
    allowed, reason = is_public_url(
        "https://evil.com.allowed.com",
        extra_blocked_hosts=["evil.com"],
    )
    assert allowed
    assert reason is None
