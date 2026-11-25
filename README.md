# SimpleFIN MCP

A **read-only** [Model Context Protocol](https://modelcontextprotocol.io) server
for the [SimpleFIN protocol](https://www.simplefin.org/protocol-v1.html), designed
to run as a [goose](https://goose-docs.ai) stdio extension.

It gives an AI agent safe, read-only access to your bank account **balances** and
**transactions** across every bank you've connected to a SimpleFIN bridge, with a
single credential — the SimpleFIN Access URL.

> ⚠️ **Read-only by design.** The SimpleFIN protocol only defines read endpoints
> (`/info`, `/accounts`, `/claim`). There is no write path in this code, and none
> is possible through the protocol. Your AI assistant can *look* at your money but
> never *move* it.

[![Packaged with uv](https://img.shields.io/badge/packaged%20with-uv-0.12.5-blue)](https://docs.astral.sh/uv/)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Why this exists

SimpleFIN lets you share bank data with third parties through a **bridge** in a
standardized, read-only way. Pointing an MCP client at it turns every connected
account into tools an agent can call — "what's my net worth?", "did any big
charges hit my cards this week?", "summarize last month's spending" — without
giving the agent any ability to transact.

This server also normalizes both flavors of the protocol:

| Area | v1.0.7 | v2.0.0 | This server |
|---|---|---|---|
| Org/account linkage | `org` (nested `Organization`) | `connections` list, `conn_id` per account | both handled, `org` object always present |
| Errors array | `errors` | `errlist` | both displayed |
| Query params | `start-date`, `end-date`, `pending` | adds `balances-only`, repeatable `account` | all supported |

## Tools

| Tool | Description |
|---|---|
| `list_accounts` | All accounts with balances. `balances_only=true` skips transactions (fast). |
| `get_transactions` | Transactions on/after `start_date` (Unix epoch seconds). Optional `end_date` (exclusive), `account` filter (comma-separated IDs), `include_pending`. |
| `check_connection` | Verifies the bridge is reachable (`GET /info`) and credentials are accepted (balances-only probe). Returns protocol versions + account count. |
| `claim_access_url` | One-time exchange of a SimpleFIN Token for the Access URL. Run once per token — after claiming, the token is spent. |

All tools return JSON. Credentials are masked in all error output (`_mask()`),
and the Access URL never appears in tool results.

## Requirements

- Python 3.10+ and [uv](https://docs.astral.sh/uv/) (spawns the server and its env)
- A SimpleFIN Token from your bank (or the [SimpleFIN bridge](https://bridge.simplefin.org/simplefin/create))
- [goose](https://goose-docs.ai) 1.60+ or any MCP client that speaks stdio

## Quick start (goose)

Full walkthrough in **[USAGE.md](USAGE.md)**. TL;DR:

1. **Claim your token** (one-time, from a terminal):
   ```bash
   uv run python -m simplefin_mcp.claim --token <SIMPLEFIN_TOKEN>
   ```
2. **Add to goose config** (`%APPDATA%\Block\goose\config\config.yaml` on Windows,
   `~/.config/goose/config.yaml` elsewhere):
   ```yaml
   extensions:
     simplefin:
       name: SimpleFIN
       cmd: uv
       args: ["--directory", "/path/to/simplefin-mcp", "run", "simplefin-mcp"]
       enabled: true
       envs: { "SIMPLEFIN_ACCESS_URL": "<ACCESS_URL_FROM_STEP_1>" }
       type: stdio
       timeout: 300
   ```
3. **Verify** — new goose session, then ask:
   ```
   check my SimpleFIN connection
   ```

## Documentation

- **[USAGE.md](USAGE.md)** — full setup & usage guide (claiming, config, secret
  storage with `env_keys`, troubleshooting, other MCP clients)
- **[SECURITY.md](SECURITY.md)** — security model, data flow, threat model
- `recipes/weekly-spending-review/recipe.yaml` — a validated goose recipe that
  wraps this extension into a weekly report workflow

## Using the server outside goose

Any MCP client that can spawn a std subprocess can run it:

```bash
SIMPLEFIN_ACCESS_URL="https://user:pass@bridge.example.com/simplefin" \
  uv --directory /path/to/simplefin-mcp run simplefin-mcp
```

The server also accepts `--access-url <URL>` or `--token <TOKEN>` flags (see
`main()`), and `--timeout <seconds>` for slow bridges.

## Development

```bash
uv sync                                  # install dependencies + package
uv run pytest                            # 22 offline unit tests (mocked HTTP)
uv run python tests_smoke_stdio.py       # in-process-tools smoke test
uv run python tests_smoke_subprocess.py  # cross-process stdio handshake (as goose runs it)
```

Tests never touch the network: unit tests use `httpx.MockTransport`; the
subprocess smoke test spaws a local `http.server` mock bridge on `127.0.0.1`.

## Project layout

```
simplefin-mcp/
├── src/simplefin_mcp/
│   ├── __init__.py
│   ├── simplefin.py       # protocol client: token decode/claim, Access URL parse, /info, /accounts
│   ├── server.py          # MCP server (mcp 2.x MCPServer) + 4 tools (stdio transport)
│   └── claim.py           # one-time claim CLI: python -m simplefin_mcp.claim
├── tests/
│   └── test_simplefin.py  # offline unit tests (httpx.MockTransport)
├── tests_smoke_stdio.py   # in-process smoke test over a mock bridge
├── tests_smoke_subprocess.py # end-to-end stdio test with the official MCP client
├── recipes/weekly-spending-review/recipe.yaml
├── USAGE.md · SECURITY.md · LICENSE
└── pyproject.toml         # console script: simplefin-mcp = simplefin_mcp.server:main
```

## Acknowledgments

- [SimpleFIN](https://www.simplefin.org/) for the protocol and bridge reference
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [goose](https://goose-docs.ai) by the Agentic AI Foundation

## License

[MIT](LICENSE)
