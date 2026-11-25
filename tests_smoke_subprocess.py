"""Cross-process smoke test: run simplefin-mcp as a subprocess the way goose does.

Launches the actual console script (`simplefin-mcp`) as a child process over
stdio, connects with MCP's official stdio client, and exercises the tools.

This validates main()'s env-config path and the transport goose will use.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import threading
import http.server

from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.session import ClientSession

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
            "transactions": [],
        }
    ],
}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path.endswith("/info"):
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


async def main() -> None:
    httpd, port = start_mock_bridge()
    access_url = f"http://demo:secret@127.0.0.1:{port}/simplefin"
    print(f"mock bridge on port {port}")

    server_params = StdioServerParameters(
        command="uv",
        args=["--directory", os.path.dirname(os.path.abspath(__file__)), "run", "simplefin-mcp"],
        env={**os.environ, "SIMPLEFIN_ACCESS_URL": access_url},
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            print("initialize ok; tools:", sorted(t.name for t in result.tools))

            check = await session.call_tool("check_connection", {})
            print("check_connection:", check.content[0].text)

            accounts = await session.call_tool("list_accounts", {"balances_only": True})
            print("list_accounts:", accounts.content[0].text)

            txns = await session.call_tool(
                "get_transactions",
                {"start_date": 978360000, "include_pending": True},
            )
            print("get_transactions:", txns.content[0].text)

    httpd.shutdown()
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    asyncio.run(main())
