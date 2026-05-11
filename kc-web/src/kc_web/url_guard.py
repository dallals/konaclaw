from __future__ import annotations
import ipaddress
import urllib.parse
from typing import Iterable


_LOCAL_SUFFIXES = (".local", ".internal", ".localhost")
_METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata"})


def is_public_url(
    url: str,
    extra_blocked_hosts: Iterable[str] = (),
) -> tuple[bool, str | None]:
    """Return (allowed, reason_if_blocked) for a candidate fetch URL.

    Allowed: http/https URLs that resolve to a public destination at the
    syntactic level. Rejected: non-http schemes, localhost / *.local /
    *.internal / *.localhost hosts, private/loopback/link-local IP literals,
    GCP metadata names, and any host in extra_blocked_hosts (exact match).

    Does NOT defend against DNS rebinding -- Firecrawl resolves on its side.
    This guard catches the obvious attack surface only.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False, "unparseable"

    if parsed.scheme not in ("http", "https"):
        return False, "non_http_scheme"

    host = parsed.hostname
    if not host:
        return False, "missing_host"

    host_lower = host.lower()

    if host_lower in _METADATA_HOSTS:
        return False, "metadata_endpoint"

    if host_lower == "localhost" or host_lower.endswith(_LOCAL_SUFFIXES):
        return False, "local_hostname"

    try:
        ip = ipaddress.ip_address(host)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False, "private_ip"
    except ValueError:
        pass  # not an IP literal — fine, fall through

    if host_lower in set(extra_blocked_hosts):
        return False, "extra_blocked"

    return True, None
