# github-mcp

Self-hosted GitHub MCP server. Personal-access-token auth, stdio transport, transport-agnostic so it works with Claude Code (any account), Codex CLI, Cursor, or any MCP-compliant client. No claude.ai OAuth dependency.

Written in Python on top of FastMCP. Slots into the same install pattern as other community Python MCP servers.

## Why this exists

The official `github/github-mcp-server` (Go, 60+ tools) is excellent if you want a kitchen-sink server tied to a single agent platform's auth flow. This one is scoped for operators who:

- Switch between multiple Claude accounts, Codex CLI, Cursor, or other MCP clients and want the same GitHub auth across all of them.
- Prefer a small, audited tool surface scoped to PR + repo file + Dependabot operations.
- Want a Python install that lives alongside their other MCP servers.

If neither applies, run `github/github-mcp-server` instead — that's the right call.

## Tool surface (v0.1.0)

| Tool | Purpose |
|---|---|
| `health_check` | Verify the PAT is valid + report rate-limit headroom. First call when wiring up. |
| `list_prs` | List pull requests with state filter. |
| `get_pr` | Single PR with check-run status + mergeable state. |
| `merge_pr` | Squash/merge/rebase with optional branch delete. |
| `list_dependabot_alerts` | Alert sweep with severity-grouped counts. |
| `get_file` | Read a file via the Contents API. |
| `put_file` | Create or update a file via the Contents API (idempotent with SHA). |

Future versions expand: workflows, releases, issues, rulesets, secret-scan alerts, branch protection, repo settings.

## Install

```bash
git clone https://github.com/adelaidasofia/github-mcp ~/.claude/github-mcp
cd ~/.claude/github-mcp
uv tool install --editable .
```

Or as a one-off without persistent install:

```bash
cd ~/.claude/github-mcp
uv run github-mcp
```

## Auth

The server reads the GitHub PAT from environment, in this precedence:

1. `GITHUB_TOKEN`
2. `GH_TOKEN`

Fine-grained tokens recommended.

### Scope matrix

| Permission | Access | Unlocks |
|---|---|---|
| Metadata | Read | Baseline (auto-required when any other repo permission is set) |
| Contents | Read and write | `get_file`, `put_file` |
| Pull requests | Read and write | `list_prs`, `get_pr`, `merge_pr` |
| Dependabot alerts | Read | `list_dependabot_alerts` |
| Administration | Read and write | Future: rulesets, branch protection, `allow_auto_merge` toggle |
| Workflows | Read and write | Future: read/update `.github/workflows/*.yml` |
| Actions | Read and write | Future: trigger and inspect workflow runs |
| Secret scanning alerts | Read | Future: fleet-wide secret-scan sweep |
| Code scanning alerts | Read | Future: CodeQL fleet sweep |

**v0.1 floor**: Metadata + Contents (R/W) + Pull requests (R/W) + Dependabot alerts (R) is enough to use every tool in this release.

**Future-proof**: granting the full table now means new MCP tools can ship without rotating the PAT. Equivalent to checking "all repository permissions" in the fine-grained PAT UI. Trade-off: broader blast radius if the token leaks. Reasonable for a personal-dev token on an encrypted laptop with FileVault; reconsider for shared or production deployments.

The MCP introspects scopes via `health_check` — call it after wiring up to confirm what your token actually has.

Optional: `GITHUB_API_BASE` for GitHub Enterprise Server. Defaults to `https://api.github.com`.

## Wire into an MCP client

### Claude Code

Add to `~/.claude/.mcp.json` (or any `.mcp.json` in the project root):

```json
{
  "mcpServers": {
    "github": {
      "command": "uv",
      "args": ["run", "--project", "/Users/<you>/.claude/github-mcp", "github-mcp"],
      "env": {
        "GITHUB_TOKEN": "${GITHUB_TOKEN}"
      }
    }
  }
}
```

### Codex CLI

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.github]
command = "uv"
args = ["run", "--project", "/Users/<you>/.claude/github-mcp", "github-mcp"]

[mcp_servers.github.env]
GITHUB_TOKEN = "${GITHUB_TOKEN}"
```

### Cursor

`~/.cursor/mcp.json` follows the Claude Code shape. Same env block.

### Any other MCP-compliant client

Configure it to spawn the `github-mcp` command and pipe stdio. The server speaks standard MCP over stdio.

## Verify

```bash
GITHUB_TOKEN=ghp_yourpat uv run github-mcp
```

The server will wait on stdin for MCP traffic. In another shell, exercise `health_check` via your client.

## Test

```bash
uv pip install -e ".[dev]"
uv run pytest tests/ -v
```

## License

MIT.
