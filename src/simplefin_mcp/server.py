"""SimpleFIN MCP server (stdio).

Exposes read-only tools over a SimpleFIN Access URL:

    Tools: list_accounts, get_transactions, claim_access_url, check_connection

The Access URL (which embeds Basic Auth credentials) is configured via the
SIMPLEFIN_ACCESS_URL environment variable - the recommended way, matching
goose's extension `envs` pattern - or via --access-url / --token CLI options.

Protocol: https://www.simplefin.org/protocol-v1.html
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

from .simplefin import (
    DEFAULT_TIMEOUT,
    SimpleFinClient,
    SimpleFinError,
    claim_token,
)

tool_header = (
    "SimpleFIN MCP: read-only bank account balances and transactions "
    "via the SimpleFIN protocol (https://www.simplefin.org/protocol-v1.html). "
    "Tools are read-only: they fetch account lists and transactions; they are "
    "not able to move money or change anything at your bank."
)

mcp = MCPServer(name="simplefin", instructions=tool_header)

_client: SimpleFinClient | None = None


def _get_client() -> SimpleFinClient:
    global _client
    if _client is None:
        access_url = os.environ.get("SIMPLEFIN_ACCESS_URL", "").strip()
        if not access_url:
            raise SimpleFinError(
                "SIMPLEFIN_ACCESS_URL is not set. Either claim a token once with the "
                "claim_access_url tool and store the returned Access URL as the "
                "SIMPLEFIN_ACCESS_URL environment variable in the extension's envs, "
                "or pass it via --access-url when starting."
            )
        _client = SimpleFinClient(access_url)
    return _client


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_accounts(balances_only: bool = False) -> str:
    """List all bank accounts with current balances.

    Args:
        balances_only: If true, skip fetching transactions entirely (the
            'balances-only=1' query parameter). Recommended for quick balance
            checks so no transaction payload is pulled.
    """
    try:
        data = _get_client().get_accounts(balances_only=balances_only)
    except SimpleFinError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(format_account_set(data), default=str)


@mcp.tool()
def get_transactions(
    start_date: int,
    end_date: int | None = None,
    account: str | None = None,
    include_pending: bool = False,
) -> str:
    """Get transactions on or after start_date (Unix epoch seconds).

    Args:
        start_date: Unix epoch timestamp in seconds. Transactions on or after
            this moment are returned.
        end_date: Optional exclusive upper bound (Unix epoch seconds).
            Transactions are restricted to ones before (but not on) this
            timestamp.
        account: Optional account-id filter, e.g. '2930002'. Pass a
            comma-separated list to filter multiple accounts at once.
        include_pending: If true, include pending transactions if supported.
            By default pending transactions are NOT included.
    """
    accounts: list[str] | None = None
    if account:
        accounts = [a.strip() for a in account.split(",") if a.strip()]
    try:
        data = _get_client().get_accounts(
            start_date=start_date,
            end_date=end_date,
            accounts=accounts,
            include_pending=include_pending,
        )
    except SimpleFinError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(format_account_set(data), default=str)


@mcp.tool()
def claim_access_url(token: str) -> str:
    """Claim an Access URL from a one-time SimpleFIN Token (run once per token).

    Base64-decodes the SimpleFIN Token into a claim URL and POSTs it. Returns
    the Access URL to store as SIMPLEFIN_ACCESS_URL in the extension's envs.
    The Access URL embeds your Basic Auth credentials - treat it as a secret;
    after storing it, restart the session; it will not be re-displayed.

    Args:
        token: The Base64-encoded SimpleFIN Token, copied from your bank's
            SimpleFIN page or from bridge.simplefin.org.
    """
    try:
        access_url = claim_token(token)
    except SimpleFinError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(
        {
            "access_url": access_url,
            "next_steps": [
                "1) Add SIMPLEFIN_ACCESS_URL=<access_url> to this extension's envs (goose configure, or edit config.yaml)",
                "2) Restart the goose session",
                "3) Run check_connection to verify authentication",
            ],
        }
    )


@mcp.tool()
def check_connection() -> str:
    """Check connectivity to the SimpleFIN bridge and validate credentials.

    Calls GET /info (unauthenticated protocol-versions endpoint) to confirm
    reachability, then GET /accounts?balances-only=1 to verify Basic Auth
    works, and returns the number of accounts found.
    """
    try:
        client = _get_client()
        info = client.get_info()
    except SimpleFinError as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    versions = info.get("versions", []) if isinstance(info, dict) else None
    try:
        data = client.get_accounts(balances_only=True)
    except SimpleFinError as exc:
        return json.dumps({"ok": False, "versions": versions, "error": str(exc)})
    accounts = data.get("accounts", []) if isinstance(data, dict) else []
    return json.dumps(
        {
            "ok": True,
            "versions": versions,
            "account_count": len(accounts),
            "message": "SimpleFIN bridge reachable; credentials accepted for /accounts.",
        },
        default=str,
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_ACCOUNT_KEYS = (
    "id",
    "name",
    "currency",
    "balance",
    "available-balance",
    "balance-date",
)
ORG_KEYS = ("domain", "sfin-url")


def format_account_set(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize a SimpleFIN AccountSet for LLM consumption.

    Handles both v1 ('org', 'errors') and v2 ('connection'/'conn_id',
    'errlist', 'connections') field variants leniently.
    """
    out: dict[str, Any] = {"accounts": []}
    errs = data.get("errlist") or data.get("errors") or []
    out["errors"] = errs if errs else []
    connections = data.get("connections")
    if connections:
        out["connections"] = connections
    for acct in data.get("accounts", []) or []:
        formatted: dict[str, Any] = {
            k: acct[k] for k in _ACCOUNT_KEYS if acct.get(k) is not None
        }
        org = acct.get("org") or acct.get("connection")
        if org:
            formatted["org"] = org if isinstance(org, str) else {
                k: org[k] for k in ORG_KEYS if org.get(k) is not None
            }
        if acct.get("conn_id") is not None:
            formatted["conn_id"] = acct["conn_id"]
        txns = acct.get("transactions")
        if txns:
            formatted["transaction_count"] = len(txns)
            formatted["transactions"] = txns
        out["accounts"].append(formatted)
    return out


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="simplefin-mcp",
        description=(
            "SimpleFIN MCP server (stdio). Set SIMPLEFIN_ACCESS_URL in the "
            "environment (recommended for goose extensions), or pass --access-url "
            "(a previously claimed Access URL) or --token (a one-time SimpleFIN "
            "Token to claim at startup)."
        ),
    )
    parser.add_argument(
        "--access-url",
        help="SimpleFIN Access URL (prefer the SIMPLEFIN_ACCESS_URL env var)",
    )
    parser.add_argument(
        "--token",
        help="One-time SimpleFIN Token: claim it at startup and cache it in memory",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="HTTP timeout in seconds (default: 30)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)

    access_url = (args.access_url or os.environ.get("SIMPLEFIN_ACCESS_URL", "")).strip()
    if args.token and not access_url:
        # One-time claim at startup (for the --token convenience path).
        try:
            access_url = claim_token(args.token, timeout=args.timeout)
            print(
                "simplefin-mcp: claimed Access URL from token (cached in memory only).",
                file=sys.stderr,
            )
        except SimpleFinError as exc:
            print(f"simplefin-mcp: {exc}", file=sys.stderr)
            sys.exit(1)
    if not access_url:
        # Still allow the server to start so the claim_access_url tool can be
        # used interactively; data tools will surface a clear error until then.
        print(
            "simplefin-mcp: SIMPLEFIN_ACCESS_URL not set; use the claim_access_url "
            "tool once, then configure it in the extension envs.",
            file=sys.stderr,
        )

    global _client
    if access_url:
        _client = SimpleFinClient(access_url, timeout=args.timeout)

    mcp.run()


if __name__ == "__main__":
    main()
