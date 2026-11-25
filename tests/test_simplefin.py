"""Unit tests for SimpleFIN MCP server utilities.

Uses httpx.MockTransport to exercise the SimpleFIN protocol logic without
hitting real bridges, plus offline tests of token decoding and URL parsing.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.parse

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from simplefin_mcp.simplefin import (
    SimpleFinClient,
    SimpleFinError,
    build_accounts_params,
    claim_token,
    decode_simplefin_token,
    parse_access_url,
)
from simplefin_mcp.server import format_account_set


# ---------------------------------------------------------------------------
# Token decoding
# ---------------------------------------------------------------------------


def _encode_token(url: str) -> str:
    pad = (4 - len(url) % 3) % 4  # base64 of str length is fine; just compute
    return base64.b64encode(url.encode()).decode()


def test_decode_simplefin_token_roundtrip():
    url = "https://bridge.simplefin.org/simplefin/claim/demo"
    token = base64.b64encode(url.encode()).decode()
    assert decode_simplefin_token(token) == url


def test_decode_simplefin_token_urlsafe_and_unpadded():
    url = "https://bridge.simplefin.org/simplefin/claim/demo==x"
    token = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    assert decode_simplefin_token(token) == url


def test_decode_simplefin_token_rejects_garbage():
    with pytest.raises(SimpleFinError):
        decode_simplefin_token("not-base64 !!!")


def test_decode_simplefin_token_rejects_non_url():
    payload = base64.b64encode(b"just some text").decode()
    with pytest.raises(SimpleFinError):
        decode_simplefin_token(payload)


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------


def test_claim_token_success(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(200, text="https://demo:secret@sfin.example.com/simplefin")

    original_post = httpx.post

    def fake_post(url, **kwargs):
        transport = httpx.MockTransport(handler)
        with httpx.Client(transport=transport, timeout=kwargs.get("timeout", 30)) as c:
            return c.post(url)

    monkeypatch.setattr(httpx, "post", fake_post)
    token = base64.b64encode(b"https://bridge.example.com/claim/t").decode()
    access_url = claim_token(token)
    assert access_url == "https://demo:secret@sfin.example.com/simplefin"


def test_claim_token_already_claimed(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    def fake_post(url, **kwargs):
        transport = httpx.MockTransport(handler)
        with httpx.Client(transport=transport) as c:
            return c.post(url)

    monkeypatch.setattr(httpx, "post", fake_post)
    token = base64.b64encode(b"https://bridge.example.com/claim/t").decode()
    with pytest.raises(SimpleFinError, match="403"):
        claim_token(token)


# ---------------------------------------------------------------------------
# Access URL parsing
# ---------------------------------------------------------------------------


def test_parse_access_url():
    url = "https://demo:secret@sfin.mybank.com/simplefin"
    base, path, user, pw = parse_access_url(url)
    assert base == "https://sfin.mybank.com"
    assert path == "/simplefin"
    assert user == "demo"
    assert pw == "secret"


def test_parse_access_url_percent_encoded():
    url = "https://user%40x:p%40ss@sfin.example.com/simplefin"
    base, path, user, pw = parse_access_url(url)
    assert user == "user@x"
    assert pw == "p@ss"


def test_parse_access_url_missing_credentials():
    with pytest.raises(SimpleFinError, match="credentials"):
        parse_access_url("https://sfin.example.com/simplefin")


def test_parse_access_url_rejects_ftp():
    with pytest.raises(SimpleFinError, match="http"):
        parse_access_url("ftp://demo:pw@sfin.example.com/simplefin")


# ---------------------------------------------------------------------------
# Query param building
# ---------------------------------------------------------------------------


def test_build_accounts_params_all():
    params = build_accounts_params(
        start_date=978360153,
        end_date=981513753,
        accounts=["a", "b"],
        include_pending=True,
        balances_only=False,
    )
    assert ("start-date", "978360153") in params
    assert ("end-date", "981513753") in params
    assert ("pending", "1") in params
    assert ("account", "a") in params
    assert ("account", "b") in params
    assert ("balances-only", "1") not in params


def test_build_accounts_params_balances_only():
    params = build_accounts_params(balances_only=True)
    assert params == [("balances-only", "1")]


# ---------------------------------------------------------------------------
# Client behavior over a mock transport
# ---------------------------------------------------------------------------

ACCESS_URL = "https://demo:secret@sfin.mybank.com/simplefin"


def _client_with(handler) -> SimpleFinClient:
    transport = httpx.MockTransport(handler)
    return SimpleFinClient(ACCESS_URL, transport=transport)


def test_client_get_info_no_auth():
    def handler(request: httpx.Request) -> httpx.Response:
        # httpx encodes auth in request headers when using Client(auth=...)
        auth = request.headers.get("Authorization")
        if request.url.path.endswith("/info"):
            assert auth is None, "/info must be unauthenticated"
            return httpx.Response(200, json={"versions": ["1.0"]})
        return httpx.Response(404)

    client = _client_with(handler)
    assert client.get_info() == {"versions": ["1.0"]}


def test_client_get_accounts_basic_auth_sent():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accounts"):
            auth = request.headers.get("Authorization", "")
            assert auth.startswith("Basic ")
            q = dict(urllib.parse.parse_qsl(request.url.query.decode()))
            assert q.get("start-date") == "978360153"
            return httpx.Response(
                200,
                json={
                    "errors": [],
                    "accounts": [
                        {
                            "org": {"domain": "mybank.com", "sfin-url": "https://sfin.mybank.com"},
                            "id": "2930002",
                            "name": "Savings",
                            "currency": "USD",
                            "balance": "100.23",
                            "available-balance": "75.31",
                            "balance-date": 978366153,
                            "transactions": [],
                        }
                    ],
                },
            )
        return httpx.Response(404)

    client = _client_with(handler)
    data = client.get_accounts(start_date=978360153)
    assert data["accounts"][0]["id"] == "2930002"
    account = data["accounts"][0]
    assert account["org"]["domain"] == "mybank.com"


def test_client_get_accounts_403_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    client = _client_with(handler)
    with pytest.raises(SimpleFinError, match="403"):
        client.get_accounts()


def test_client_get_accounts_402_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402)

    client = _client_with(handler)
    with pytest.raises(SimpleFinError, match="402"):
        client.get_accounts()


def test_client_error_messages_mask_credentials():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _client_with(handler)
    try:
        client.get_accounts()
        raise AssertionError("expected SimpleFinError")
    except SimpleFinError as exc:
        assert "demo:secret" not in str(exc)
        assert "demo" not in str(exc)


def test_client_get_accounts_non_json_content_type():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>oops</html>", headers={"content-type": "text/html"})

    client = _client_with(handler)
    with pytest.raises(SimpleFinError, match="content-type"):
        client.get_accounts()


def test_client_accounts_multiple_account_filters():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accounts"):
            q = urllib.parse.parse_qsl(request.url.query.decode())
            seen.append(q)
        return httpx.Response(200, json={"errors": [], "accounts": []})

    client = _client_with(handler)
    client.get_accounts(accounts=["111", "222"])
    assert seen[0].count(("account", "111")) == 1
    assert seen[0].count(("account", "222")) == 1


# ---------------------------------------------------------------------------
# Account set formatting
# ---------------------------------------------------------------------------


def test_format_account_set_v1():
    data = {
        "errors": [],
        "accounts": [
            {
                "org": {"domain": "mybank.com", "sfin-url": "https://sfin.mybank.com"},
                "id": "2930002",
                "name": "Savings",
                "currency": "USD",
                "balance": "100.23",
                "available-balance": "75.23",
                "balance-date": 978366153,
                "transactions": [
                    {"id": "t1", "posted": 978360100, "amount": "-4.00", "description": "Coffee Shop"}
                ],
            }
        ],
    }
    out = format_account_set(data)
    assert out["errors"] == []
    acct = out["accounts"][0]
    assert acct["id"] == "2930002"
    assert acct["balance"] == "100.23"
    assert acct["org"]["domain"] == "mybank.com"
    assert acct["transaction_count"] == 1
    assert acct["transactions"][0]["description"] == "Coffee Shop"


def test_format_account_set_v2_fields():
    data = {
        "errlist": [],
        "connections": [{"name": "My Bank", "domain": "mybank.com"}],
        "accounts": [
            {
                "connection": {"name": "My Bank", "domain": "mybank.com"},
                "conn_id": "abc123",
                "id": "2930002",
                "name": "Checking",
                "currency": "USD",
                "balance": "12.00",
                "balance-date": 978366153,
            }
        ],
    }
    out = format_account_set(data)
    assert out["errors"] == []
    assert out["connections"] == [{"name": "My Bank", "domain": "mybank.com"}]
    acct = out["accounts"][0]
    assert acct["conn_id"] == "abc123"
    assert acct["org"] == {"domain": "mybank.com"}
    assert "transactions" not in acct and "transaction_count" not in acct


def test_format_account_set_empty():
    out = format_account_set({"accounts": []})
    assert out == {"accounts": [], "errors": []}
