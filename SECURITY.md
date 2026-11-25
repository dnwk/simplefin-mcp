# Security Policy

SimpleFIN MCP is a **read-only** bridge between an MCP client (goose et al.) and
your bank data served over the
[SimpleFIN protocol](https://www.simplefin.org/protocol-v1.html). This document
describes the security model, the trust boundaries, and safe-handling rules.

## Security model

### 1. Read-only by protocol and by code

- The SimpleFIN protocol only defines read endpoints (`GET /info`,
  `GET /accounts`) plus the one-time claim (`POST /claim/:token`).
- This server implements **only** those calls. There is no write, transfer, or
  payment path — anywhere — and none is possible through the Access URL, even
  if a model attempted it. (See the渗透 threat model below.)
- Tools exposed: `list_accounts`, `get_transactions`, `check_connection`
  (read-only), and `claim_access_url` (one-time token exchange).

### 2. Credential handling

- The **Access URL embeds HTTP Basic Auth credentials**
  (`https://user:pass@bridge.example.com/simplefin`). Those credentials grant
  read access to all connected accounts.
- The Access URL reaches this server via the `SIMPLEFIN_ACCESS_URL`
  environment variable — either goose's `envs` config or encrypted secret
  storage via `env_keys`. It is **never** passed as a tool argument.
- All error messages are scrubbed with `_mask()`: the credential portion of the
  URL cannot appear in any tool result or error, and therefore cannot leak into
  the LLM context or chat logs. (Unit tests assert this.)
- The Access URL is never persisted by this server — no logs, no cache files.

### 3. Token claim hygiene

- The one-time claim is recommended to be run by the human, from a terminal,
  via `python -m simplefin_mcp.claim` — outside any AI chat.
- A `claim_access_url` tool exists for convenience, but claiming never happens
  silently: the Access URL is printed for the human to copy into their secret
  storage / config.
- Tokens are spent after a single claim; the CLI and tool both make this
  explicit.

### 4. Network posture

- Local **stdio subprocess**: goose spawns the server; the server accepts
  JSON-RPC on stdin/stdout only. **No inbound network exposure** (no ports,
  no LAN, no Internet listening).
- Outbound: exactly one HTTPS host — your SimpleFIN bridge.
- No telemetry, analytics, or third-party calls of any kind.

## Trust boundaries

```
[ Your bank ]  <--- HTTPS (bank-controlled) --->  [ SimpleFIN bridge ]
                                                        |
                                                   HTTPS (Basic Auth)
                                                        |
                                              [ simplefin-mcp subprocess ]
                                                        |  stdio (JSON-RPC)
                                                        |
                                              [ goose / MCP client ]
                                                        |
                                              [ LLM provider (your choice) ]
```

- Data is exposed to the LLM only through tool results: account metadata,
  balances, transaction descriptions. Distinguish: account numbers, balances,
  and transaction payees are private financial data — they transit your LLM
  provider in tool results, exactly like any text you paste into chat.
- Credentials never transit the LLM: env var at spawn time + masking in errors.

## Safe-handling rules for users

1. Treat the Access URL like a bank password. Store it in goose secret storage
   (`env_keys`) when possible; otherwise keep `config.yaml` out of synced,
   backed-up-to-cloud, or git folders (see USAGE.md § 5).
2. Generate tokens in a browser; claim them in a terminal. Avoid pasting tokens
   into AI chats.
3. Rotate by claiming a fresh token; revoke via your bank's/bridge's procedure.
4. This repo's `.gitignore` blocks `token.txt`, `access_url.txt`, and common
   financial-data dumps — never bypass it with `git add -f`.

## Reporting a vulnerability

Please open a private security advisory or contact the maintainer via GitHub
(https://github.com/dnwk/simplefin-mcp/security/advisories). Do not open a
public issue for security problems.

---

SimpleFIN MCP · [README](README.md) · [USAGE.md](USAGE.md)
