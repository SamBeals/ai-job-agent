"""URL safety / SSRF protection tests — no real network."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.agents.scout.ingestion.models import UnsafeURLError
from app.agents.scout.ingestion.url_safety import (
    assert_redirect_target_safe,
    canonicalize_url,
    validate_public_http_url,
)


def test_https_url_allowed() -> None:
    with patch("app.agents.scout.ingestion.url_safety._assert_hostname_public"):
        assert validate_public_http_url("https://example.com/jobs/1").startswith("https://")


def test_http_url_allowed() -> None:
    with patch("app.agents.scout.ingestion.url_safety._assert_hostname_public"):
        validate_public_http_url("http://example.com/jobs/1")


def test_file_scheme_rejected() -> None:
    with pytest.raises(UnsafeURLError):
        validate_public_http_url("file:///etc/passwd", resolve=False)


def test_localhost_rejected() -> None:
    with pytest.raises(UnsafeURLError):
        validate_public_http_url("http://localhost/jobs", resolve=False)


def test_127_0_0_1_rejected() -> None:
    with pytest.raises(UnsafeURLError):
        validate_public_http_url("http://127.0.0.1/jobs", resolve=False)


def test_private_192_rejected() -> None:
    with pytest.raises(UnsafeURLError):
        validate_public_http_url("http://192.168.1.10/jobs", resolve=False)


def test_private_10_rejected() -> None:
    with pytest.raises(UnsafeURLError):
        validate_public_http_url("http://10.0.0.5/jobs", resolve=False)


def test_link_local_rejected() -> None:
    with pytest.raises(UnsafeURLError):
        validate_public_http_url("http://169.254.169.254/latest/meta-data", resolve=False)


def test_redirect_to_private_rejected() -> None:
    with pytest.raises(UnsafeURLError):
        assert_redirect_target_safe("http://127.0.0.1/secret")


def test_hostname_resolving_to_private_rejected() -> None:
    import socket

    # Fake getaddrinfo returning private IP
    fake = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 0))]
    with patch("socket.getaddrinfo", return_value=fake):
        with pytest.raises(UnsafeURLError):
            validate_public_http_url("https://evil.example/jobs", resolve=True)


def test_canonicalize_strips_fragment() -> None:
    with patch("app.agents.scout.ingestion.url_safety._assert_hostname_public"):
        # canonicalize doesn't resolve
        from urllib.parse import urlparse

        url = "https://Example.COM/jobs/1#section"
        parsed = urlparse(url)
        assert parsed.hostname
        canon = canonicalize_url(url)
        assert "#" not in canon
        assert "example.com" in canon
