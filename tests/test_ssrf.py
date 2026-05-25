"""SSRF mitigation tests for github-mcp client.request (MYC-101).

The fetch site is github_mcp/client.py::request. Path can be absolute
(user-supplied URL for pagination next-links) or relative (appended to
api.github.com base). Either way, the resolved URL passes through
sanitize_or_raise + assert_public_ip before httpx fires, and
follow_redirects=False blocks 3xx that could land on a private IP.
"""
from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from github_mcp.client import request


# Patch _get_token so we don't need a real PAT for these tests.
@pytest.fixture(autouse=True)
def _stub_token():
    with patch("github_mcp.client._get_token", return_value="dummy-pat"):
        yield


class TestSSRFGitHubClient:
    """Five-test matrix: backslash / embedded-creds / IPv6-link-local /
    DNS-private / AWS-metadata. Pre-fetch helper must reject all five."""

    @pytest.mark.asyncio
    async def test_rejects_url_with_backslash(self):
        with pytest.raises(RuntimeError, match="SSRF"):
            await request("GET", "https://api.github.com/\\evil")

    @pytest.mark.asyncio
    async def test_rejects_embedded_credentials(self):
        with pytest.raises(RuntimeError, match="SSRF"):
            await request("GET", "https://user:pass@api.github.com/repos/x/y")

    @pytest.mark.asyncio
    async def test_rejects_ipv6_link_local(self):
        with pytest.raises(RuntimeError, match="SSRF"):
            await request("GET", "http://[fe80::1]/x")

    @pytest.mark.asyncio
    async def test_rejects_dns_resolving_to_private_ip(self):
        with patch("mycelium_security.url.socket.getaddrinfo") as mock_resolver:
            mock_resolver.return_value = [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0))
            ]
            with pytest.raises(RuntimeError, match="SSRF"):
                await request("GET", "https://attacker.example.com/")

    @pytest.mark.asyncio
    async def test_rejects_aws_metadata_endpoint(self):
        with pytest.raises(RuntimeError, match="SSRF"):
            await request(
                "GET", "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
            )


class TestRedirectBlocked:
    """follow_redirects=False is wired on httpx.AsyncClient — a 302 from a
    public GitHub URL to a private IP returns the 302 response unchanged,
    callers see status 302 + Location header rather than the redirect target."""

    @pytest.mark.asyncio
    async def test_follow_redirects_false_is_set(self):
        # Inspect the client config indirectly by mocking httpx.AsyncClient.
        import httpx
        captured = {}

        class _SpyAsyncClient(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)
                super().__init__(*args, **kwargs)

        with patch("github_mcp.client.httpx.AsyncClient", _SpyAsyncClient):
            try:
                await request("GET", "https://api.github.com/zen")
            except Exception:
                # network failure / auth is fine; we only care about init kwargs
                pass

        assert captured.get("follow_redirects") is False
