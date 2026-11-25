"""SimpleFIN protocol client (read-only).

Implements the parts of the SimpleFIN protocol needed by the MCP server:

    GET  {base}{path}/info      supported protocol versions (no authentication)
    GET  {base}{path}/accounts  account and transaction data (HTTP Basic Auth)
                                query params: start-date, end-date, pending,
                                account (repeatable), balances-only

Token flow (run once per token):

    SimpleFIN Token  = Base64-encoded claim URL
    POST claim URL    -> Access URL (a URL with embedded Basic Auth credentials)

Protocol reference: https://www.simplefin.org/protocol-v1.html
"""

from __future__ import annotations

import base64
import re
import urllib.parse
from typing import Any

import httpx

# Server-side aggregation of many accounts can take well over 30s on the beta
# bridge (measured ReadTimeout at 30s while serving 37 accounts of history).
DEFAULT_TIMEOUT = 90.0

_TOKEN_RE = re.compile(r"^[A-Za-z0-9+/\-_]+=*$")
_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


class SimpleFinError(Exception):
    """A SimpleFIN failure. Messages are safe to surface to the LLM/user."""


def decode_simplefin_token(token: str) -> str:
    """Decode a SimpleFIN Token (Base64-encoded URL) into its claim URL."""
    token = token.strip()
    norm = token.replace("-", "+").replace("_", "/")
    norm += "=" * (-len(norm) % 4)
    if not _TOKEN_RE.match(norm):
        raise SimpleFinError(
            "Invalid SimpleFIN Token: not valid Base64. "
            "Paste the token exactly as copied from your bank's SimpleFIN page."
        )
    try:
        decoded = base64.b64decode(norm).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise SimpleFinError(f"Invalid SimpleFIN Token: decode failed ({exc}).") from exc
    if not _SCHEME_RE.match(decoded):
        raise SimpleFinError(
            "Decoded token is not an http(s) URL. The token looks truncated - copy the full token."
        )
    return decoded


def claim_token(token: str, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Claim the Access URL for a one-time SimpleFIN Token.

    The token is Base64-decoded into a claim URL, which is POSTed once.
    Returns the Access URL, which embeds Basic Auth credentials.
    """
    claim_url = decode_simplefin_token(token)
    try:
        resp = httpx.post(claim_url, timeout=timeout)
    except httpx.HTTPError as exc:
        raise SimpleFinError(
            f"Network error while claiming the token ({type(exc).__name__})."
        ) from exc
    if resp.status_code == 404:
        raise SimpleFinError(
            "Claim failed with HTTP 404: invalid or already-claimed token. Generate a new token."
        )
    if resp.status_code == 403:
        raise SimpleFinError(
            "Claim failed with HTTP 403: the token appears to have been disabled."
        )
    if resp.status_code >= 400:
        raise SimpleFinError(
            f"Claim failed with HTTP {resp.status_code}. "
            "The token may already be claimed, revoked, or invalid - generate a new one."
        )
    access_url = resp.text.strip()
    if access_url.startswith('"') and access_url.endswith('"'):
        access_url = access_url[1:-1]
    if not _SCHEME_RE.match(access_url):
        raise SimpleFinError(
            f"The claim endpoint did not return a URL (got: {access_url[:120]})."
        )
    return access_url


def parse_access_url(access_url: str) -> tuple[str, str, str, str]:
    """Split an Access URL into (base, path, username, password).

    The Access URL has the form:
        https://user:password@host/path
    Credentials may be percent-encoded; they are decoded here.
    """
    parsed = urllib.parse.urlsplit(access_url)
    if parsed.scheme not in ("http", "https"):
        raise SimpleFinError("Access URL must be an http(s) URL.")
    netloc = parsed.netloc
    if "@" not in netloc:
        raise SimpleFinError(
            "Access URL is missing Basic Auth credentials (user:password@host form). "
            "Claim a fresh token and store the full Access URL it returns."
        )
    credentials_raw, host = netloc.rsplit("@", 1)
    if not credentials_raw:
        raise SimpleFinError("Access URL has empty credentials.")
    user_raw, sep, pass_raw = credentials_raw.partition(":")
    username = urllib.parse.unquote(user_raw)
    password = urllib.parse.unquote(pass_raw) if sep else ""
    base = urllib.parse.urlunsplit((parsed.scheme, host, "", "", ""))
    return base, parsed.path.rstrip("/"), username, password


def build_accounts_params(
    start_date: int | None = None,
    end_date: int | None = None,
    accounts: list[str] | None = None,
    include_pending: bool = False,
    balances_only: bool = False,
) -> list[tuple[str, str]]:
    """Build the query parameters for GET /accounts.

    Parameter semantics (SimpleFIN protocol):
      - start-date:  transactions on or after this Unix epoch timestamp
      - end-date:    transactions before (not on) this Unix epoch timestamp
      - pending=1:   include pending transactions (default: NOT included)
      - account:     restrict to the given account id (repeatable)
      - balances-only=1: skip transaction data entirely
    """
    params: list[tuple[str, str]] = []
    if start_date is not None:
        params.append(("start-date", str(int(start_date))))
    if end_date is not None:
        params.append(("end-date", str(int(end_date))))
    if include_pending:
        params.append(("pending", "1"))
    for account in accounts or []:
        if account:
            params.append(("account", account))
    if balances_only:
        params.append(("balances-only", "1"))
    return params


class SimpleFinClient:
    """Read-only HTTP client for a SimpleFIN bridge using an Access URL."""

    def __init__(
        self,
        access_url: str,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        base, path, username, password = parse_access_url(access_url)
        self.base = base
        self.path = path
        self.timeout = timeout
        self._secret = f"{username}:{password}"
        self._http = httpx.Client(
            base_url=base,
            auth=(username, password),
            transport=transport,
            timeout=timeout,
            headers={"User-Agent": "simplefin-mcp/0.1.0 (goose extension)"},
        )
        self._http_noauth = httpx.Client(
            base_url=base,
            transport=transport,
            timeout=timeout,
            headers={"User-Agent": "simplefin-mcp/0.1.0 (goose extension)"},
        )

    def close(self) -> None:
        self._http.close()
        self._http_noauth.close()

    # -- helpers ------------------------------------------------------------

    def _mask(self, message: str) -> str:
        """Ensure no credentials leak into an error message."""
        return message.replace(self._secret, "***")

    def _request(self, http: httpx.Client, path: str, params: list[tuple[str, str]]) -> httpx.Response:
        try:
            return http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise SimpleFinError(
                self._mask(f"Network error contacting the SimpleFIN bridge ({type(exc).__name__}).")
            ) from exc

    def _json(self, resp: httpx.Response, what: str) -> Any:
        if resp.status_code == 403:
            raise SimpleFinError(
                "403 Authentication failed: access has been revoked or credentials are incorrect."
            )
        if resp.status_code == 402:
            raise SimpleFinError("402 Payment required by the SimpleFIN bridge.")
        if resp.status_code >= 400:
            raise SimpleFinError(
                f"SimpleFIN bridge returned HTTP {resp.status_code} for {what}."
            )
        ctype = resp.headers.get("content-type", "")
        if "json" not in ctype.lower():
            raise SimpleFinError(
                f"Unexpected content-type '{ctype}' from the SimpleFIN bridge; expected JSON."
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise SimpleFinError(f"Invalid JSON from the SimpleFIN bridge: {exc}") from exc

    # -- endpoints -----------------------------------------------------------

    def get_info(self) -> Any:
        """GET /info: protocol versions the server supports (no authentication)."""
        resp = self._request(self._http_noauth, self.path + "/info", [])
        return self._json(resp, "/info")

    def get_accounts(
        self,
        start_date: int | None = None,
        end_date: int | None = None,
        accounts: list[str] | None = None,
        include_pending: bool = False,
        balances_only: bool = False,
    ) -> Any:
        """GET /accounts: retrieve account and transaction data."""
        params = build_accounts_params(
            start_date=start_date,
            end_date=end_date,
            accounts=accounts,
            include_pending=include_pending,
            balances_only=balances_only,
        )
        resp = self._request(self._http, self.path + "/accounts", params)
        return self._json(resp, "/accounts")
