from kc_web.client import FirecrawlError, WebClientError


def test_web_client_error_carries_status_and_message():
    e = WebClientError(429, "rate limited")
    assert e.status == 429
    assert e.message == "rate limited"
    assert "429" in str(e)
    assert "rate limited" in str(e)


def test_firecrawl_error_is_alias_of_web_client_error():
    assert FirecrawlError is WebClientError
