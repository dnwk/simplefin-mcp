# Using SimpleFIN MCP

Full lifecycle: getting a token, claiming it into an Access URL, wiring the
server into goose, troubleshooting. For the security model, see
[SECURITY.md](SECURITY.md); for an overview, see the [README](README.md).

## Table of contents

1. [Concepts](#1-concepts)
2. [Getting your Access URL](#2-getting-your-access-url)
3. [Installing the server with goose](#3-installing-the-server-with-goose)
4. [Verifying and using the tools](#4-verifying-and-using-the-tools)
5. [Secret storage option (env_keys)](#5-secret-storage-option-env_keys)
6. [Scheduling automated reviews](#6-scheduling-automated-reviews)
7. [Using in other MCP clients](#7-using-in-other-mcp-clients)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Concepts

```
Your banks ──> SimpleFIN bridge ──> this MCP server ──> goose / MCP client ──> you
                (aggregated data)    (stdio subprocess,      (plain-English
                                      read-only GETs)          questions)
```

**SimpleFIN Token** — a one-time Base64 token issued by your bank's SimpleFIN
`/create` page. It can be claimed exactly once.

**Access URL** — what you get for the token: `https://user:pass@bridge.example.com/simplefin`.
The `user:pass` portion is an HTTP Basic Auth credential for READ access to all
your connected accounts. Treat it like a bank password.

**This server** — a Python MCP server goose spawns as a local subprocess. It
only makes read-only `GET` calls to the bridge, plus the one-time claim `POST`.

## 2. Getting your Access URL

### Step 1 — Get a SimpleFIN Token

Visit your bank's SimpleFIN `/create` page, or the public
[SimpleFIN bridge create page](https://bridge.simplefin.org/simplefin/create),
and copy the token. Do this in a browser — keep the token out of AI chats.

### Step 2 — Claim the token (one-time)

From a terminal, in the project directory:

```bash
uv run python -m simplefin_mcp.claim --token <SIMPLEFIN_TOKEN>
```

or, keeping the token out of your shell history:

```bash
uv run python -m simplefin_mcp.claim --token-file token.txt
```

This POSTs to the claim URL decoded from the token and prints the Access URL.
**The token can only be claimed once.** If yours was already spent, generate a
fresh token at your bank's `/create` page and claim that one instead.

Lost the Access URL? Same answer: claim a fresh token. Revocation is done via
your bank's/bridge's revoke procedure.

## 3. Installing the server with goose

goose reads its extension config once at session start. The config file lives
at `%APPDATA%\Block\goose\config\config.yaml` on Windows,
`~/.config/goose/config.yaml` on Linux/macOS.

### Option A — config file

Add under `extensions:`:

```yaml
extensions:
  simplefin:
    name: SimpleFIN
    cmd: uv
    args: ["--directory", "/path/to/simplefin-mcp", "run", "simplefin-mcp"]
    enabled: true
    type: stdio
    envs: { "SIMPLEFIN_ACCESS_URL": "<ACCESS_URL_FROM_STEP_2>" }
    timeout: 300
```

Use the absolute path where you cloned this repo (Windows example:
`args: ["--directory", "D:\\gooseagent\\simplefin-mcp", "run", "simplefin-mcp"]`).

### Option B — `goose configure` CLI

```bash
goose configure
# Add Extension -> Command-Line Extension
# command: uv --directory /path/to/simplefin-mcp run simplefin-mcp
# env vars: SIMPLEFIN_ACCESS_URL=<your access url>
```

### Option C — one-off session

```bash
goose session --with-extension "SIMPLEFIN_ACCESS_URL=<ACCESS_URL> uv --directory /path/to/simplefin-mcp run simplefin-mcp"
```

### Config timing note (why a live session may say "not configured")

If you paste the Access URL **while a session is already running**, that
session's subprocess was spawned without it and its tools will report that the
access URL isn't configured. Fix: start a new session, or in the live session
ask goose to disable → re-enable the simplefin extension (verified on
goose 1.60.2, Windows).

## 4. Verifying and using the tools

New goose session, then ask: `check my SimpleFIN connection`

Expected:

```json
{"ok": true, "versions": ["1.0"], "account_count": 37,
 "message": "SimpleFIN bridge reachable; credentials accepted for /accounts."}
```

### Tool reference

#### `list_accounts(balances_only: bool = False)`

All accounts with id, name, currency, balance, available-balance, balance-date,
org. `balances_only=true` skips transactions (fast snapshot). With the default
(slow path) accounts include full `transactions` — with many accounts and no
date window this can be large; prefer `get_transactions` with a `start_date`
for history.

#### `get_transactions(start_date, end_date=None, account=None, include_pending=False)`

- `start_date` / `end_date` — Unix epoch **seconds** (start inclusive, end exclusive)
- `account` — one ID or comma-separated IDs
- `include_pending=true` — include unposted transactions

Example: "show my transactions from the last 14 days for account ACT-EXAMPLE-ID…"
— say it in words; the agent computes the epoch values.

Results normalize protocol v1 and v2: always per-account `org` object, plus a
convenience `transaction_count`. `errors` (v1) / `errlist` (v2) are surfaced
whichever is present.

#### `claim_access_url(token)`

One-time claim, exposed as a tool for convenience — same as the terminal CLI.
The printed Access URL is for YOU to copy into config/secret storage; don't
store it in chat.

#### `check_connection()`

No arguments. Probes `GET /info` (unauthenticated) and a balances-only
`/accounts` (authenticated). Reports `ok`, `versions`, `account_count`.

## 5. Secret storage option (env_keys)

To keep the Access URL out of `config.yaml` entirely, use goose's encrypted
secret storage and reference only the key name:

```yaml
extensions:
  simplefin:
    name: SimpleFIN
    cmd: uv
    args: ["--directory", "/path/to/simplefin-mcp", "run", "simplefin-mcp"]
    enabled: true
    type: stdio
    env_keys: [SIMPLEFIN_ACCESS_URL]
    timeout: 300
```

Then store the value once in goose's secure storage (see the goose docs for the
secrets commands in your version — `goose configure` also reaches secure
storage). The config file then contains no bank credentials at all.

## 6. Scheduling automated reviews

A validated goose recipe ships at
[`recipes/weekly-spending-review/recipe.yaml`](recipes/weekly-spending-review/recipe.yaml).

```bash
goose recipe validate recipes/weekly-spending-review/recipe.yaml
goose schedule add recipes/weekly-spending-review/recipe.yaml --cron "0 8 * * FRI"
```

The recipe uses `env_keys: [SIMPLEFIN_ACCESS_URL]`, so credentials resolve from
goose's secret storage at run time and the recipe file stays shareable.

## 7. Using in other MCP clients

Any stdio MCP client can spawn the server:

```bash
SIMPLEFIN_ACCESS_URL="https://user:pass@bridge.example.com/simplefin" \
  uv --directory /path/to/simplefin-mcp run simplefin-mcp
```

Flags exist too: `--access-url <URL>`, `--token <TOKEN>` (claims at startup),
`--timeout <seconds>` for slow bridges.

Claude Desktop example (`claude_desktop_config.json`) — check your client's
env-var convention:

```json
{
  "mcpServers": {
    "simplefin": {
      "command": "uv",
      "args": ["--directory", "/path/to/simplefin-mcp", "run", "simplefin-mcp"],
      "env": { "SIMPLEFIN_ACCESS_URL": "https://user:pass@bridge.example.com/simplefin" }
    }
  }
}
```

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "The SimpleFIN access URL isn't configured" | Session started before the URL was saved | New session, or disable → re-enable the extension |
| `Unknown tool simplefin__check_connection` | Extension didn't start / wrong snapshot | `goose info` to check config parses; ensure `enabled: true`; retry next turn |
| ReadTimeout / network errors | Bridge slow (aggregation on request) | Retry; narrow the date window; raise `--timeout` (default 90 s) |
| HTTP `403` | Credentials rejected/revoked | Claim fresh token → new Access URL |
| HTTP `402` | Payment required (bridge policy) | Check the bridge's plan |
| `errors`/`errlist` in response | Per-bank connection issue at the bridge | Read the array entries |
| Oversized/slow responses | All accounts, no window | `balances_only=true` for snapshots; window `get_transactions` |
| Timezone/epoch confusion | Dates are Unix seconds | Phrase it in natural language; let the agent compute |

## FAQ

**Can the AI move my money?**
No — the protocol only defines read endpoints; there is no write path.

**Where is my Access URL stored?**
In goose config `envs` (plain file under your OS account) or encrypted secret
storage via `env_keys`. Never in git, chat logs, or tool output (errors are
mask-scrubbed).

**Can I revoke access?**
Yes — via your bank's/bridge's revoke procedure; the embedded credentials stop
working.

**Registered tool names?**
`simplefin__list_accounts`, `simplefin__get_transactions`,
`simplefin__claim_access_url`, `simplefin__check_connection`.

---

SimpleFIN MCP · [README](README.md) · [SECURITY.md](SECURITY.md) · [LICENSE](LICENSE)
