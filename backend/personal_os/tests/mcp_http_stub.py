"""A remote (streamable HTTP) MCP server for the tests: `python mcp_http_stub.py <port>`.

One tool, `daily`, answering the way fitness servers do: a JSON document per date.
"""
from __future__ import annotations

import json
import sys

import uvicorn
from mcp.server.mcpserver import MCPServer

server = MCPServer(name="http-stub", version="0.1")


@server.tool()
def daily(date: str) -> str:
    """Daily health summary for one day (YYYY-MM-DD)."""
    return json.dumps({"date": date, "total_steps": 8123, "resting_heart_rate_bpm": 52})


if __name__ == "__main__":
    uvicorn.run(server.streamable_http_app(), host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
