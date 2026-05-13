"""Thin GitHub REST API client. PAT-auth via env var, httpx transport.

Auth precedence: GITHUB_TOKEN > GH_TOKEN. Both supported because the official
gh CLI uses GH_TOKEN; many ecosystems standardize on GITHUB_TOKEN. We accept
either so the same token works wherever the user already has one configured.

Base URL is fixed to api.github.com. Enterprise Server support is a deferred
v0.2 concern; users on GHES can override via GITHUB_API_BASE if needed.
"""

from __future__ import annotations

import base64
import os
from typing import Any

import httpx


def _get_token() -> str:
    """Resolve the PAT from env. Fails loud if absent so the caller knows."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError(
            "github-mcp: no GitHub PAT found. Set GITHUB_TOKEN (or GH_TOKEN) "
            "in the environment. Fine-grained tokens preferred. Minimum "
            "scopes for the v0.1 tool surface: repo, workflow."
        )
    return token


def _base_url() -> str:
    return os.environ.get("GITHUB_API_BASE", "https://api.github.com").rstrip("/")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "github-mcp/0.1.0",
    }


async def request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    accept: str | None = None,
) -> httpx.Response:
    """Single HTTP call. Returns the raw Response so the caller decides how to
    consume it (status header inspection for 204 paths, raw bytes for blobs).

    Path may be absolute (https://...) or relative (/repos/...); both work.
    """
    url = path if path.startswith("http") else f"{_base_url()}{path if path.startswith('/') else '/' + path}"
    headers = _headers()
    if accept:
        headers["Accept"] = accept

    async with httpx.AsyncClient(timeout=30.0) as cx:
        return await cx.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
        )


async def request_json(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> Any:
    """Convenience wrapper for JSON-returning endpoints. Raises on 4xx/5xx with
    a body excerpt so the agent gets a real error message instead of a status
    code."""
    resp = await request(method, path, params=params, json_body=json_body)
    if resp.status_code >= 400:
        body = resp.text[:600]
        raise RuntimeError(
            f"github api {method} {path} -> {resp.status_code}: {body}"
        )
    if resp.status_code == 204 or not resp.text:
        return None
    return resp.json()


def encode_content_b64(text: str) -> str:
    """Contents API expects base64-encoded file content."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def decode_content_b64(b64: str) -> str:
    """Decode the Contents API response field. Strips wrapping newlines."""
    cleaned = b64.replace("\n", "").replace("\r", "")
    return base64.b64decode(cleaned).decode("utf-8")
