"""Claim a SimpleFIN Token's Access URL from the command line.

Usage:
    uv run python -m simplefin_mcp.claim --token <SIMPLEFIN_TOKEN>
    uv run python -m simplefin_mcp.claim --token-file token.txt

The Access URL embeds Basic Auth credentials - store it securely. (The
SimpleFIN protocol requires you to store Access URLs at least as securely as
the user's financial data.)
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m simplefin_mcp.claim",
        description="Claim a SimpleFIN Token into an Access URL (one-time).",
    )
    parser.add_argument("--token", help="The Base64-encoded SimpleFIN Token")
    parser.add_argument(
        "--token-file",
        help="Path to a file containing the token (avoids putting it in shell history)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the Access URL, for scripting",
    )
    args = parser.parse_args(argv)

    token = args.token
    if not token and args.token_file:
        with open(args.token_file, "r", encoding="utf-8") as f:
            token = f.read().strip()
    if not token:
        parser.error("provide --token or --token-file")

    from .simplefin import claim_token

    try:
        access_url = claim_token(token)
    except Exception as exc:  # SimpleFinError
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.quiet:
        print(access_url)
        return 0

    print("Access URL claimed successfully.\n")
    print(f"  {access_url}\n")
    print("Store this Access URL securely. For goose, add it to the extension's")
    print("envs in config.yaml:")
    print('    envs: { "SIMPLEFIN_ACCESS_URL": "<access_url>" }')
    print("\nThis token can only be claimed once; if you lose the Access URL you")
    print("will need to generate a new token at your bank's SimpleFIN page.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
