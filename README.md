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

## Secret hygiene (read this first)

**v0.1.1+ reads the PAT from macOS Keychain by default.** Env-var fallback is still supported for non-macOS / CI, but the Keychain path is the recommended setup because the secret never enters argv or process env — safe from `ps aux`, `pgrep -fl`, `lsof`, `/proc/<pid>/environ`, panic dumps, CI logs, debuggers, or any LLM agent running an introspection command.

```bash
# One-time setup (macOS):
security add-generic-password -s github-mcp -a "$USER" -w "<your-fine-grained-PAT>"

# Update later (e.g. after rotation):
security delete-generic-password -s github-mcp -a "$USER" 2>/dev/null
security add-generic-password -s github-mcp -a "$USER" -w "<new-PAT>"
```

**Do NOT** install with an inline `--env`:

```bash
# BAD — bakes the PAT into ~/.claude.json + every Claude child spawn's --mcp-config argv.
# Any pgrep / ps aux dumps it.
claude mcp add github --scope user --env GITHUB_TOKEN=github_pat_... -- <command>

# GOOD — no --env block. The server reads from Keychain at call time.
claude mcp add github --scope user -- <command>
```

PR/incident lineage: this hygiene path was added 2026-05-19 after a `pgrep -fl chrome-devtools-mcp` dumped a PAT-bearing claude process into a session transcript. See [adelaidasofia/github-mcp Keychain migration]() (TODO: link PR once merged).

For non-macOS environments, fall back to env:

```bash
export GITHUB_TOKEN=<your-PAT>   # set in shell rc, NOT in argv
```

Minimum scopes for the v0.1 tool surface: Contents R/W, Pull requests R/W, Dependabot R.

## Install

Open Claude Code, paste:

    /plugin marketplace add adelaidasofia/github-mcp
    /plugin install github-mcp@github-mcp

<details><summary>Legacy install</summary>

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

</details>

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


## Telemetry

This plugin sends a single anonymous install signal to `myceliumai.co` the first time it loads in a Claude Code session on a given machine.

**What is sent:**
- Plugin name (e.g. `slack-mcp`)
- Plugin version (e.g. `0.1.0`)

**What is NOT sent:**
- No user identifiers, names, emails, tokens, or API keys
- No file paths, message content, or anything from your work
- No IP address is stored after dedup processing

**Why:** Helps the maintainer know which plugins people actually install, so attention goes to the ones that get used.

**Opt out:** Set the environment variable `MYCELIUM_NO_PING=1` before launching Claude Code. The hook will skip the network call entirely. Already-pinged installs leave a sentinel at `~/.mycelium/onboarded-<plugin>` — delete it if you want to reset state.

## License

MIT.
