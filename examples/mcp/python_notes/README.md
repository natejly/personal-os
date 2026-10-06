# python_notes

A small MCP server over stdio with two tools:

- `add_note(text)` saves a note in memory.
- `list_notes()` lists them. It is marked `readOnlyHint`, which Grain shows but never trusts: the tool
  still asks.

Notes live only as long as the process; Grain starts it again when the connection is recycled.

## Run it

```bash
uv run server.py                 # installs `mcp` for this run from the script header
# or, with the mcp package already installed:
python server.py
```

It then waits for an MCP client on stdin. To see the tools without Grain:

```bash
python - <<'PY'
import asyncio, sys
from mcp import ClientSession, StdioServerParameters, stdio_client

async def main():
    async with stdio_client(StdioServerParameters(command=sys.executable, args=["server.py"])) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print([t.name for t in (await s.list_tools()).tools])

asyncio.run(main())
PY
```

## Add it to Grain

Library → Connectors → Add custom, transport `stdio`. Use the absolute path to the script.

Command line:

```
uv run /absolute/path/to/examples/mcp/python_notes/server.py
```

or the same as a JSON block:

```json
{
  "mcpServers": {
    "notes": {
      "command": "uv",
      "args": ["run", "/absolute/path/to/examples/mcp/python_notes/server.py"]
    }
  }
}
```

Press Check: it should list `add_note` and `list_notes`. Both start at `ask`; ask a chat to "add a note
saying hello" and approve the card.

`uv` must be on the PATH Grain can see (see "Stdio servers and PATH" in
[docs/connectors.md](../../../docs/connectors.md)). If you use `python` instead, give the full path to a
Python that has `mcp` installed.
