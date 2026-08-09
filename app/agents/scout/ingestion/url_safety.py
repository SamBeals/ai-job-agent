"""SSRF-aware URL validation for manual job ingestion."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse, urlunparse

from app.agents.scout.ingestion.models import UnsafeURLError


_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
}


def canonicalize_url(url: str) -> str:
    """Normalize URL for duplicate detection (lowercase host, strip fragment)."""
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise UnsafeURLError("I couldn't evaluate that URL because it isn't a valid HTTP/HTTPS job URL.")
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return urlunparse((parsed.scheme.lower(), netloc, path.rstrip("/") or "/", "", parsed.query, ""))


def validate_public_http_url(url: str, *, resolve: bool = True) -> str:
    """Validate URL scheme/host and optionally resolve DNS to public IPs.

    Returns the stripped URL if safe.
    """
    raw = (url or "").strip()
    if not raw:
        raise UnsafeURLError("I couldn't evaluate that URL because it isn't a valid HTTP/HTTPS job URL.")

    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError(
            "I couldn't evaluate that URL because it isn't a valid HTTP/HTTPS job URL."
        )
    if not parsed.hostname:
        raise UnsafeURLError("I couldn't evaluate that URL because it isn't a valid HTTP/HTTPS job URL.")

    host = parsed.hostname.lower().rstrip(".")
    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost") or host.endswith(".local"):
        raise UnsafeURLError(
            "That URL points to a local/private network address and cannot be fetched."
        )

    # Literal IP in hostname
    try:
        ip = ipaddress.ip_address(host)
        if not _is_public_ip(ip):
            raise UnsafeURLError(
                "That URL points to a local/private network address and cannot be fetched."
            )
    except ValueError:
        # hostname — resolve if requested
        if resolve:
            _assert_hostname_public(host)

    if parsed.username or parsed.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed.")

    return raw


def assert_redirect_target_safe(url: str) -> None:
    """Re-validate a redirect destination (always resolve)."""
    validate_public_http_url(url, resolve=True)


def _assert_hostname_public(hostname: str) -> None:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(
            f"Scout couldn't resolve host `{hostname}` for that job URL."
        ) from exc

    if not infos:
        raise UnsafeURLError(f"Scout couldn't resolve host `{hostname}` for that job URL.")

    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if not _is_public_ip(ip):
            raise UnsafeURLError(
                "That URL points to a local/private network address and cannot be fetched."
            )


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
        return False
    if ip.is_reserved or ip.is_unspecified:
        return False
    # Cloud metadata commonly 169.254.169.254 (link-local) already covered.
    # Also block unique-local IPv6 etc. via is_private.
    if isinstance(ip, ipaddress.IPv4Address):
        # Explicit CGNAT / documentation ranges already private/reserved.
        if ip in ipaddress.ip_network("169.254.0.0/16"):
            return False
    return True
