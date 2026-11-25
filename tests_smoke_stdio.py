"""In-process smoke test: exercise simplefin-mcp tools against a local mock bridge.

Unlike tests_smoke_subprocess.py, this runs in one process against the server
object, which is handy for debugging tool behavior quickly.

Run:  uv run python tests_smoke_stdio.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import threading
import http.server

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import simplefin_mcp.server as server_mod
from simplefin_mcp.simplefin import SimpleFinClient
from simplefin_mcp.server import mcp as server

ACCOUNTS = {
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
                {
                    "id": "txn-1",
                    "posted": 978360100,
                    "amount": "-4.00",
                    "description": "Coffee Shop",
                }
            ],
        }
    ],
}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path.endswith("/info"):  # unauthenticated per the protocol
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"versions": ["1.0"]}).encode())
            return
        auth = self.headers.get("Authorization", "")
        expected = base64.b64encode(b"demo:secret").decode()
        if not auth.startswith("Basic ") or auth.split(" ", 1)[1] != expected:
            self.send_response(403)
            self.end_headers()
            return
        if path.endswith("/accounts"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(ACCOUNTS).encode())
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *a):
        pass


def start_mock_bridge():
    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


async def smoke() -> None:
    httpd, port = start_mock_bridge()
    access_url = f"http://demo:secret@127.0.0.1:{port}/simplefin"
    os.environ["SIMPLEFIN_ACCESS_URL"] = access_url
    server_mod._client = SimpleFinClient(access_url)

    tools = await server.list_tools()
    print("tools/list:", [t.name for t in tools])

    for label, call in [
        ("check_connection", ("check_connection", {})),
        ("list_accounts", ("list_accounts", {"balances_only": True})),
        ("get_transactions", ("get_transactions", {"start_date": 978360000})),
    ]:
        name, args = call
        result = await server.call_tool(name, args)
        content = result.structured_content["result"]
        assert '"error"' not in content, f"{label} failed: {content}"
        print(f"{label}: {content}")

    # Error path: unreachable bridge
    server_mod._client = SimpleFinClient("http://demo:secret@127.0.0.1:1/simplefin")
    result = await server.call_tool("check_connection", {})
    print("unreachable-bridge path:", result.structured_content["result"])

    # Error path: access URL not configured
    del os.environ["SIMPLEFIN_ACCESS_URL"]
    server_mod._client = None
    result = await server.call_tool("check_connection", {})
    print("unset-env path:", result.structured_content["result"])

    httpd.shutdown()
    print("IN-PROCESS SMOKE TEST PASSED")


if __name__ == "__main__":
    asyncio.run(smoke())
