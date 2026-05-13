"""github-mcp server entrypoint.

FastMCP setup + tool registration. Stdio transport by default; FastMCP also
exposes HTTP/SSE for clients that want them (Codex CLI, Cursor, Manus when it
ships MCP, etc).

v0.1.0 tool surface:
    health_check        — verify the PAT works and report rate-limit headroom
    list_prs            — list PRs with the same filters gh pr list uses
    get_pr              — single PR with status checks + mergeable state
    merge_pr            — squash/merge/rebase with optional auto-merge wait
    list_dependabot_alerts — alert sweep, the canonical use case from the 27-alert incident
    get_file            — read a file from a repo at a ref (defaults to default branch)
    put_file            — create or update a file via the Contents API (idempotent via SHA)

Future versions expand: workflows, releases, issues, rulesets, secret-scan
alerts, branch protection, repo settings. Out of v0.1 scope.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from .client import (
    decode_content_b64,
    encode_content_b64,
    request,
    request_json,
)

mcp = FastMCP("github-mcp")


@mcp.tool()
async def health_check() -> dict[str, Any]:
    """Verify the GitHub PAT is valid and report current rate-limit state.

    Returns the authenticated login, token scopes (when discoverable), the
    rate-limit ceiling, remaining requests, and the reset timestamp. Use this
    as the first call when wiring github-mcp into a new client.
    """
    resp = await request("GET", "/user")
    if resp.status_code != 200:
        return {
            "ok": False,
            "status": resp.status_code,
            "body": resp.text[:400],
        }
    user = resp.json()
    return {
        "ok": True,
        "login": user.get("login"),
        "scopes": resp.headers.get("x-oauth-scopes", "").split(", ") if resp.headers.get("x-oauth-scopes") else [],
        "rate_limit": {
            "limit": int(resp.headers.get("x-ratelimit-limit", 0)),
            "remaining": int(resp.headers.get("x-ratelimit-remaining", 0)),
            "reset": int(resp.headers.get("x-ratelimit-reset", 0)),
        },
    }


@mcp.tool()
async def list_prs(
    owner: str,
    repo: str,
    state: str = "open",
    per_page: int = 30,
) -> list[dict[str, Any]]:
    """List pull requests for a repo.

    Args:
        owner: GitHub owner/org name (e.g. "adelaidasofia").
        repo: Repository name.
        state: "open" | "closed" | "all". Default "open".
        per_page: Page size, capped at 100 server-side.

    Returns a list of compact PR records (number, title, state, head, base,
    author, draft, mergeable_state when available, html_url).
    """
    data = await request_json(
        "GET",
        f"/repos/{owner}/{repo}/pulls",
        params={"state": state, "per_page": min(per_page, 100)},
    )
    return [
        {
            "number": p["number"],
            "title": p["title"],
            "state": p["state"],
            "draft": p.get("draft", False),
            "author": p["user"]["login"] if p.get("user") else None,
            "head": p["head"]["ref"],
            "base": p["base"]["ref"],
            "url": p["html_url"],
            "created_at": p["created_at"],
            "updated_at": p["updated_at"],
        }
        for p in data
    ]


@mcp.tool()
async def get_pr(owner: str, repo: str, number: int) -> dict[str, Any]:
    """Fetch a single PR with its check-run status and mergeable state.

    Args:
        owner: GitHub owner/org name.
        repo: Repository name.
        number: PR number.

    Returns the PR record plus a `checks` array (each item: name, status,
    conclusion) and `mergeable`, `mergeable_state` fields. Use this before
    merge_pr to confirm CI is green.
    """
    pr = await request_json("GET", f"/repos/{owner}/{repo}/pulls/{number}")
    checks_data = await request_json(
        "GET",
        f"/repos/{owner}/{repo}/commits/{pr['head']['sha']}/check-runs",
    )
    checks = [
        {
            "name": c["name"],
            "status": c["status"],
            "conclusion": c.get("conclusion"),
        }
        for c in (checks_data or {}).get("check_runs", [])
    ]
    return {
        "number": pr["number"],
        "title": pr["title"],
        "state": pr["state"],
        "draft": pr.get("draft", False),
        "head_sha": pr["head"]["sha"],
        "head_ref": pr["head"]["ref"],
        "base_ref": pr["base"]["ref"],
        "mergeable": pr.get("mergeable"),
        "mergeable_state": pr.get("mergeable_state"),
        "author": pr["user"]["login"] if pr.get("user") else None,
        "url": pr["html_url"],
        "body": pr.get("body", ""),
        "checks": checks,
    }


@mcp.tool()
async def merge_pr(
    owner: str,
    repo: str,
    number: int,
    method: str = "squash",
    delete_branch: bool = True,
) -> dict[str, Any]:
    """Merge a pull request.

    Args:
        owner: GitHub owner/org name.
        repo: Repository name.
        number: PR number.
        method: "squash" | "merge" | "rebase". Default "squash".
        delete_branch: Whether to delete the head branch after merge. Default true.

    No "auto-merge wait" loop here — the caller should call get_pr first to
    confirm CI status, then call merge_pr. Keeps tool surface deterministic.
    """
    if method not in ("squash", "merge", "rebase"):
        raise ValueError(f"merge method must be squash|merge|rebase, got {method!r}")

    result = await request_json(
        "PUT",
        f"/repos/{owner}/{repo}/pulls/{number}/merge",
        json_body={"merge_method": method},
    )

    deleted = False
    if delete_branch and result and result.get("merged"):
        pr = await request_json("GET", f"/repos/{owner}/{repo}/pulls/{number}")
        ref = pr["head"]["ref"]
        del_resp = await request("DELETE", f"/repos/{owner}/{repo}/git/refs/heads/{ref}")
        deleted = del_resp.status_code in (200, 204)

    return {
        "merged": bool(result and result.get("merged")),
        "sha": result.get("sha") if result else None,
        "branch_deleted": deleted,
    }


@mcp.tool()
async def list_dependabot_alerts(
    owner: str,
    repo: str,
    state: str = "open",
    per_page: int = 100,
) -> dict[str, Any]:
    """List Dependabot alerts for a repo with a severity-grouped summary.

    Args:
        owner: GitHub owner/org name.
        repo: Repository name.
        state: "open" | "fixed" | "dismissed" | "auto_dismissed" | "all".
            Default "open" — the operational case.
        per_page: Page size, capped at 100.

    Returns {alerts: [...], counts_by_severity: {critical, high, medium, low}}.
    Mirrors the API call that surfaced the 27-alert incident on mycelium-site.
    """
    data = await request_json(
        "GET",
        f"/repos/{owner}/{repo}/dependabot/alerts",
        params={"state": state, "per_page": min(per_page, 100)},
    )
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    alerts = []
    for a in data or []:
        sev = (a.get("security_advisory") or {}).get("severity", "low")
        if sev in counts:
            counts[sev] += 1
        alerts.append({
            "number": a["number"],
            "severity": sev,
            "package": (a.get("security_vulnerability") or {}).get("package", {}).get("name"),
            "ecosystem": (a.get("security_vulnerability") or {}).get("package", {}).get("ecosystem"),
            "vulnerable_range": (a.get("security_vulnerability") or {}).get("vulnerable_version_range"),
            "fixed_in": ((a.get("security_vulnerability") or {}).get("first_patched_version") or {}).get("identifier"),
            "ghsa": (a.get("security_advisory") or {}).get("ghsa_id"),
            "summary": (a.get("security_advisory") or {}).get("summary"),
            "url": a.get("html_url"),
        })
    return {"alerts": alerts, "counts_by_severity": counts, "total": len(alerts)}


@mcp.tool()
async def get_file(
    owner: str,
    repo: str,
    path: str,
    ref: str | None = None,
) -> dict[str, Any]:
    """Read a file from a repo via the Contents API.

    Args:
        owner: GitHub owner/org name.
        repo: Repository name.
        path: File path within the repo, no leading slash.
        ref: Branch, tag, or SHA. Default: the default branch.

    Returns {content, sha, size, encoding, path}. content is decoded UTF-8
    text. sha is the blob SHA you need to pass to put_file when updating.
    """
    params = {"ref": ref} if ref else None
    data = await request_json(
        "GET",
        f"/repos/{owner}/{repo}/contents/{path}",
        params=params,
    )
    if isinstance(data, list):
        raise RuntimeError(f"{path!r} is a directory; this tool reads files only")
    text = decode_content_b64(data.get("content", "")) if data.get("content") else ""
    return {
        "path": data["path"],
        "sha": data["sha"],
        "size": data["size"],
        "encoding": data.get("encoding"),
        "content": text,
    }


@mcp.tool()
async def put_file(
    owner: str,
    repo: str,
    path: str,
    content: str,
    message: str,
    branch: str | None = None,
    sha: str | None = None,
) -> dict[str, Any]:
    """Create or update a file via the Contents API. Idempotent when sha is
    correctly supplied for updates.

    Args:
        owner: GitHub owner/org name.
        repo: Repository name.
        path: File path within the repo, no leading slash.
        content: UTF-8 file content. Will be base64-encoded for transport.
        message: Commit message.
        branch: Target branch. Default: the default branch.
        sha: Required when updating; the current blob SHA from get_file.
            Omit for create.

    Returns {created: bool, updated: bool, sha, commit_sha, path}.
    """
    body: dict[str, Any] = {
        "message": message,
        "content": encode_content_b64(content),
    }
    if branch:
        body["branch"] = branch
    if sha:
        body["sha"] = sha

    data = await request_json(
        "PUT",
        f"/repos/{owner}/{repo}/contents/{path}",
        json_body=body,
    )
    return {
        "created": sha is None,
        "updated": sha is not None,
        "sha": data["content"]["sha"],
        "commit_sha": data["commit"]["sha"],
        "path": data["content"]["path"],
    }


def main() -> None:
    """Entry point for `github-mcp` script. Runs the stdio MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
