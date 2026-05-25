"""Thin GitHub REST API client. PAT-auth via Keychain (preferred) or env var.

Auth precedence:
  1. macOS Keychain entry: service=github-mcp, account=$USER
     Read via `security find-generic-password -s github-mcp -a $USER -w`.
     Secret never enters argv or env — safe from `ps aux`, `pgrep -fl`,
     `lsof`, `/proc/<pid>/environ`, panic dumps, CI logs, debuggers.
  2. GITHUB_TOKEN env var (legacy / non-macOS fallback)
  3. GH_TOKEN env var (gh CLI ecosystem fallback)

Past incident (2026-05-19): inlined the PAT into ~/.claude.json MCP config
`env` block. Every Claude child spawn baked the PAT into a --mcp-config argv
string. A `pgrep -fl chrome-devtools-mcp` dumped the running claude process
including the PAT into the session transcript. Defensive scrubbing layer
caught it but only post-leak. The Keychain pattern removes the leak surface
entirely. See ⚙️ Meta/rules/secrets-in-keychain.md (vault) and Critical
Failure Inventory 2026-05-19 row for the full lineage.

Base URL is fixed to api.github.com. Enterprise Server support is a deferred
v0.2 concern; users on GHES can override via GITHUB_API_BASE if needed.
"""

from __future__ import annotations

import base64
import os
import subprocess
from typing import Any
from urllib.parse import urlparse

import httpx

from mycelium_security import UnsafeURL, assert_public_ip, sanitize_or_raise


def _get_token_from_keychain() -> str | None:
    """Read PAT from macOS Keychain. Returns None on any failure (non-macOS,
    no keychain entry, security CLI absent, timeout). Caller falls back to env.
    """
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                "github-mcp",
                "-a",
                os.environ.get("USER", ""),
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    token = (result.stdout or "").strip()
    return token or None


def _get_token() -> str:
    """Resolve the PAT. Keychain preferred (no argv/env exposure); env fallback
    for non-macOS or unmigrated setups. Fails loud if absent."""
    token = _get_token_from_keychain()
    if token:
        return token

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token

    raise RuntimeError(
        "github-mcp: no GitHub PAT found.\n"
        "Preferred (macOS, no leak surface via ps/env):\n"
        '  security add-generic-password -s github-mcp -a "$USER" -w "<PAT>"\n'
        "  # then restart Claude Code so the MCP server picks it up\n\n"
        "Fallback (non-macOS / legacy):\n"
        "  export GITHUB_TOKEN=<PAT>   # NOT recommended on shared machines\n\n"
        "Fine-grained tokens preferred. Minimum scopes: Contents R/W, "
        "Pull requests R/W, Dependabot R."
    )


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

    # SSRF hardening (MYC-101): sanitize URL chars/scheme, assert public IP,
    # block 3xx redirects (a redirect could land on a private/metadata IP and
    # bypass the IP check).
    try:
        safe_url = sanitize_or_raise(url)
        host = urlparse(safe_url).hostname or ""
        assert_public_ip(host)
    except UnsafeURL as exc:
        raise RuntimeError(f"github-mcp refused (SSRF): {exc}") from exc

    headers = _headers()
    if accept:
        headers["Accept"] = accept

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as cx:
        return await cx.request(
            method,
            safe_url,
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
