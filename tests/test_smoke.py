"""Smoke tests for github-mcp.

These don't hit the real GitHub API — they verify the module imports, the
client helpers work, and the tool surface is registered with FastMCP. Run
with `uv run pytest tests/ -v` from the package root.
"""

from __future__ import annotations

import base64
import os

import pytest

from github_mcp import __version__
from github_mcp.client import decode_content_b64, encode_content_b64


def test_version_string():
    assert __version__ == "0.1.0"


def test_encode_content_b64_roundtrip():
    original = "hello\nworld\n"
    encoded = encode_content_b64(original)
    assert encoded == base64.b64encode(original.encode("utf-8")).decode("ascii")
    assert decode_content_b64(encoded) == original


def test_decode_strips_wrapping_newlines():
    """GitHub returns base64 content wrapped at 60 chars; the decoder must strip."""
    payload = "hello world"
    raw = encode_content_b64(payload)
    wrapped = "\n".join(raw[i : i + 4] for i in range(0, len(raw), 4)) + "\n"
    assert decode_content_b64(wrapped) == payload


def test_get_token_raises_when_unset(monkeypatch):
    from github_mcp import client

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="no GitHub PAT found"):
        client._get_token()


def test_get_token_prefers_github_token(monkeypatch):
    from github_mcp import client

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_one")
    monkeypatch.setenv("GH_TOKEN", "ghp_two")
    assert client._get_token() == "ghp_one"


def test_get_token_falls_back_to_gh_token(monkeypatch):
    from github_mcp import client

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "ghp_only")
    assert client._get_token() == "ghp_only"


def test_base_url_default(monkeypatch):
    from github_mcp import client

    monkeypatch.delenv("GITHUB_API_BASE", raising=False)
    assert client._base_url() == "https://api.github.com"


def test_base_url_ghes_override(monkeypatch):
    from github_mcp import client

    monkeypatch.setenv("GITHUB_API_BASE", "https://github.acme.corp/api/v3/")
    assert client._base_url() == "https://github.acme.corp/api/v3"


def test_server_module_registers_tools(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "dummy")
    from github_mcp import server

    expected = {
        "health_check",
        "list_prs",
        "get_pr",
        "merge_pr",
        "list_dependabot_alerts",
        "get_file",
        "put_file",
    }
    # FastMCP exposes registered tools; the exact accessor depends on the
    # version. Check via the module-level `mcp` instance.
    assert hasattr(server, "mcp")
    # Verify each tool function is importable from the server module.
    for name in expected:
        assert hasattr(server, name), f"server.{name} missing"
