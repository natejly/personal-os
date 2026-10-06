# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2"]
# ///
"""A tiny MCP server over stdio: two tools, notes kept in memory.

Run: uv run server.py   (or: python server.py, with the `mcp` package installed)
"""
try:
    from mcp.server.mcpserver import MCPServer as Server  # mcp 2.x (FastMCP was renamed)
except ImportError:
    from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
from mcp.types import ToolAnnotations

app = Server("notes")
notes: list[str] = []  # gone when the process exits; Grain restarts it on reconnect


@app.tool()
def add_note(text: str) -> str:
    """Save a short note and return its number."""
    notes.append(text)
    return f"saved note {len(notes)}"


@app.tool(annotations=ToolAnnotations(read_only_hint=True))
def list_notes() -> str:
    """List every saved note, one per line."""
    return "\n".join(f"{i}. {n}" for i, n in enumerate(notes, 1)) or "no notes yet"


if __name__ == "__main__":
    app.run()  # stdio; stdout carries the protocol, so log to stderr only
