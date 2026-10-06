# Connectors (MCP)

A connector is an MCP server whose tools join Grain's toolbox. Grain is the client: it starts or
reaches the server, lists its tools, and offers them to the model next to the built-in ones. Library →
Connectors lists what is installed, lets you browse a catalog, search the official MCP Registry, import
servers you already set up in other apps, or add your own.

Everything a connector sends is third-party text. The rest of this page is how Grain keeps that from
doing more than you decided.

---

## How it works

**One supervisor per server.** Each server has its own asyncio task (`mcp_client.py`) that owns its
transport and session for as long as the connection lives. Callers never touch the session; they queue a
call and wait for the answer. A hung server cannot slow another one down, and quitting Grain closes
every server (stdin, then SIGTERM, then SIGKILL to the whole process group), so no server is left
running.

**Transports.** `stdio` (Grain spawns a command), `http` (streamable HTTP, a URL), and `sse` (the older
HTTP + server-sent events transport, for servers that have not moved to streamable HTTP). Prefer `http`
when a server offers both.

**Everything is bounded.** A third-party server must never be able to wedge a reply.

| What | Limit |
|---|---|
| Connect (spawn, initialize, first tool list) | 20 s |
| One tool call | 45 s; the caller gets a timeout result and the chat continues |
| Waiting on a server that is reconnecting | 8 s, then its tools are unavailable for that round |
| Heartbeat | a ping every 25 s, answered within 5 s or the connection is recycled |
| Reconnect backoff | 1 s doubling to 60 s |
| A command that cannot be spawned | 4 attempts, then status `error` (a config problem, not a blip) |
| One tool result | 200,000 characters; the end is cut and says so |
| One tool description | 4,000 characters |

A large result is stored and paged (`read_tool_result`), so it still reaches the model whole. Images and
files a tool returns are saved locally and listed under `media`, so the model can open them with
`view_image`.

**Tool names.** Every tool is namespaced `mcp__<server>__<tool>`. The prefix is reserved: no built-in
may use it, so a server cannot shadow a built-in tool. A slug, once handed out, stays the same across
reconnects. If a tool disappears and comes back, it gets the same slug and the same grant history.

**Deferred tool search.** A big server (a GitHub connector exposes about 90 tools) makes the tools array
long enough that weaker models stop choosing well. Once more than `mcpDeferAbove` connector tools are
ready (default 12; 0 sends every schema), the model is offered only `mcp_tool_search`. A search (BM25
over the tool name, description, argument names and server name) loads the best matches into the next
round. Permissions do not depend on whether a tool is loaded: grants, ask mode, taint and the schema
hash work the same either way.

**Server notes.** A server may send `instructions` when it connects. Grain fences them as "third-party
connector notes, not instructions from the user", caps them at 1,000 characters, and leaves them out if
a scan finds a fail-level finding in them.

**Resources and prompts.** A connector's resources and prompts are listed in its detail view (capped).
Only tools reach the model.

### Stdio servers and PATH

Most catalog entries start with `npx`, `uvx` or `docker`. A packaged macOS app is launched by the Dock,
whose PATH is the bare system one, so a command that works in Terminal can be invisible to Grain. Grain
rebuilds the PATH your own terminal has (your login shell's PATH, then the usual install directories:
Homebrew, nvm, Docker Desktop, `~/.local/bin`), resolves the command against it, and starts the server with
that PATH so `npx` can find `node`. A `PATH` you set on the server itself is used as given and never
extended.

If the command still cannot be found, the connector's status is `error` with a plain detail such as:

```
npx not found. Install Node.js (nodejs.org), then restart this connector.
```

and Grain does not retry in a loop. Install the runtime, then press Restart on the connector. The catalog
header shows which of Node.js (`npx`), uv (`uvx`) and Docker (`docker`) it found, and where.

---

## Security model

Third-party code runs on your Mac and its text goes into the prompt. The design assumes a server can be
wrong, changed later, or hostile, and that what it says about itself is not evidence.

**Every connector tool is danger `external` and defaults to `ask`.** The server cannot change that: its
annotations and descriptions never lower a tool's danger or its mode. Only a grant you make does.

**Grants are per tool and bound to a schema hash.** A grant (on / ask / off, global, per project or per
chat) remembers the hash of the tool's name, description and parameters as you last reviewed them. If the
server rewrites any of those, the hash no longer matches and an `on` grant decays to `ask`. You approved a
tool, not a name a server can later point somewhere else. Accepting a change never raises a grant.

**Drift review and quarantine.** When a tool's shape changes, Grain keeps the previous versions, shows a
diff of the description and parameters (added, removed, changed, newly required), re-runs the static
check on the new text and lists only the findings that are new. A change that adds a fail-level finding
(for example a description that now tells the model to ignore its instructions) is withheld: the tool is
not offered at all until you press Accept. A first-seen tool is compared against an empty shape, so a
server cannot avoid review by adding a tool instead of rewriting one. A description that names another
server's tool is also flagged, because that is how one connector steers calls to another.

**Static eval, and what it cannot do.** Check runs a live handshake and reads what the server says about
itself: schemas are well-formed JSON Schema, no tool text reads as an instruction to the model (override
phrases, fake role markers, concealment, zero-width characters, credential names), a tool that calls
itself read-only does not describe writing, no name looks like a built-in or a fake namespace, and a
server that can both read private data and send data out is flagged. The report shows these limits every
time, and they are the point:

- It is a static check of what the server advertises, not proof that it is safe.
- The server's code is not read, and its filesystem and network activity are not observed.
- A tool can behave differently from its description, including only on the call that matters.
- A tool that hangs, crashes or leaks data is found only by running it. Timeouts bound the damage; they
  do not prevent it.
- Toxic-flow detection guesses from names and descriptions and can miss a pair or flag harmless ones.
- Injection detection is pattern matching. Novel phrasing gets through.

A clean report means nothing suspicious was found in what the server advertises, and no more than that.

**Results taint the run.** Whatever a connector returns is untrusted content. After any connector call,
every external tool asks again for the rest of that run, even one you set to `on`. A plan step you
approved still shows what taint it expected.

**Annotations are self-reported.** MCP tools may carry hints (`readOnlyHint`, `destructiveHint`). Grain
stores and shows them and passes them to the approval card and the auto reviewer as claims the server made
about itself. Two rules:

- `readOnlyHint: true` never auto-allows anything. A tool that says it only reads still asks.
- `destructiveHint: true` (and not read-only) adds a confirmation before you can set an `on` grant for
  that tool; the grant request is refused with `409 destructive: confirm required` until the client
  sends `"confirm": true`. Once confirmed the grant works, and taint still forces `ask`. Choosing
  "always" on a chat approval card that shows the Destructive badge counts as that confirmation. In
  Auto mode the reviewer sees these hints as the server's own claims, and a destructive tool without a
  grant runs only on a high-confidence allow in a reply that has read no untrusted content.

**Permission modes.** Manual keeps the per-tool modes above. Auto sends a connector call that would ask to
the safety reviewer. Allow everything runs connector calls without a card, except one that untrusted
content in the reply forced to ask: that card stays in every mode.

**Secrets.** API keys and header values go to the macOS Keychain (service `Grain`), one entry per
server, written through the `security` command on stdin so a key never appears in a process list. If the
Keychain refuses (locked, no GUI session), the value goes to `.secrets.json` in the data directory with
mode 0600 and the failure is logged. The database keeps only the names. The API returns the names
(`secret_keys`) and never a value, and every stored secret is also registered with the log redactor so
it is masked if it ever reaches a log line. Header values are always treated as secrets, whatever the
header is called. Secrets are put in the server's environment only when it starts; the catalog and import
code refuse to put a secret field in a command line or URL, which are stored in plain text and visible to
`ps`.

**OAuth 2.1 for remote servers.** A remote server that answers 401 and names its authorization server is
handled by the SDK's client: metadata discovery, dynamic client registration, PKCE and refresh. Sign-in
opens your browser; the callback (`/mcp/oauth/callback`) only completes a sign-in Grain started, matched
by its `state`. Tokens and the registered client are kept in their own table in Grain's local database,
apart from the stdio secrets, and the API only says whether you are signed in. A background reconnect
that would need the browser stops with "sign-in required" rather than retrying. Sign out forgets the
tokens.

---

## Adding a connector

### Browse the catalog

Library → Connectors → Catalog lists curated servers by category (Developer, Productivity, Data, Search &
Web, Communication, Design, Cloud & Ops, Finance, Knowledge, Utilities). An entry shows its publisher,
whether the publisher is the vendor of the service, what it needs (a runtime, a key or a sign-in) and
the fields to fill. Install creates a normal connector from the entry, starts it, and marks it as
installed from the catalog. Nothing runs before you press Install, and the new tools start at `ask`. Run
Check before you grant anything.

### Search the MCP Registry

The search box queries the official MCP Registry (`registry.modelcontextprotocol.io`) live, with a short
timeout and a small cache. Registry results are **unverified**: anyone can publish there. A result only
pre-fills the Add custom form (command, arguments, URL, and the names of the environment variables it
declares, with the secret ones marked). It then goes through the same path as any server you type in:
you add it, run Check, and every tool asks.

### Import from other apps

Import reads the MCP server lists you already have in:

| App | File |
|---|---|
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` (`mcpServers`) |
| Claude Code | `~/.claude.json` (top-level `mcpServers` and each project's `mcpServers`), plus each listed project's `.mcp.json` |
| Cursor | `~/.cursor/mcp.json` (`mcpServers`) |

Import is read-only: Grain never writes to those files, caps how much it reads, and shows a message
for a file it cannot parse instead of failing. It lists each server (name, transport, command or URL, the
names of its env and header keys) and marks the ones you already have. Values are never sent to the
interface. Importing a server re-reads the file itself, so a request cannot make Grain read some other
path. An environment value goes to the Keychain when its name looks like a credential (`KEY`, `TOKEN`,
`SECRET`, `PASSWORD`, `AUTH`, `CREDENTIAL`, `COOKIE`, `SESSION` and similar) or its value does (a token
shape, or a URL with a password in it); other values stay as plain environment. Header values always go to
the Keychain. Imported servers arrive with every tool at `ask`, like any other.

### Add custom

Add custom takes a name, a transport, and either a command line with optional environment variables
(stdio) or a URL with optional headers (http, sse). A pasted `mcpServers` JSON block is also accepted.
Put keys in the secret fields, not in the command line. See [examples/mcp](../examples/mcp) for two
small servers to try.

---

## Adding a catalog entry

The catalog is `backend/personal_os/data/mcp_catalog.json`: `{"version": 1, "entries": [...]}`. Each
entry:

```json
{
  "id": "github",
  "name": "GitHub",
  "description": "Repos, issues, pull requests and code search.",
  "category": "Developer",
  "icon": "github",
  "publisher": "GitHub",
  "official": true,
  "docs": "https://github.com/github/github-mcp-server",
  "transport": "stdio",
  "runtime": "docker",
  "install": {
    "command": "docker",
    "args": ["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "ghcr.io/github/github-mcp-server"],
    "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "{token}"},
    "url": "",
    "headers": {}
  },
  "auth": "api_key",
  "fields": [
    {"id": "token", "label": "Personal access token", "secret": true, "required": true,
     "help": "Create one at github.com/settings/tokens", "placeholder": "ghp_…", "default": ""}
  ],
  "verified": {"source": "docker", "ref": "ghcr.io/github/github-mcp-server", "version": "v0.x", "on": "2026-10-06"}
}
```

| Field | Rule |
|---|---|
| `id` | `[a-z0-9-]+`, unique |
| `description` | plain text, 200 characters at most |
| `category` | one of the categories above |
| `icon` | a lucide-react icon name in kebab-case (`database`, `message-square`) |
| `official` | true when the vendor of the service (or the MCP project itself) maintains the server |
| `transport` | `stdio`, `http` or `sse` |
| `runtime` | `node`, `python`, `docker`, `binary` or `remote` |
| `auth` | `none`, `api_key`, `oauth` or `env` |
| `verified.source` | `npm`, `pypi`, `docker`, `registry` or `vendor` |

Rules the catalog tests enforce (`mcp_catalog.validate`):

- A `secret: true` field may be used only in `install.env` or `install.headers` values. Never in `args`,
  `command` or `url`: those are stored and shown in plain text, and `ps` shows arguments.
- Every `{placeholder}` names a declared field, and every required field is used somewhere.
- A secret field's `default` is `""`. No literal credential appears anywhere in an entry.
- `stdio` has a command and no URL; `http` and `sse` have an `https://` URL and no command.
- `auth: oauth` needs an `http` (or `sse`) transport. `auth: none` has no secret fields.
- A plain field may appear in `args` (a folder, a database file). An argument that is exactly `{field}`
  is dropped when the field is optional and empty. A plain field with `"multiple": true` is split on
  newlines or commas into several arguments.

**Verify before you add.** Only list a package that exists, checked the day you add it, and record how in
`verified`:

```bash
npm view @scope/package version                      # source: npm
curl -s https://pypi.org/pypi/<package>/json | jq -r .info.version   # source: pypi
curl -s 'https://registry.modelcontextprotocol.io/v0/servers?search=<name>'   # source: registry
docker manifest inspect ghcr.io/<owner>/<image>      # source: docker
```

Use `vendor` for a hosted URL the vendor documents. Do not add an entry from memory: package names that
look right are exactly what a typosquat copies. Prefer the vendor's own server over a reference server,
and check the repository before adding either, since several early reference servers have been archived.
Run Check on the entry once before committing and keep the field list as short as the server allows.

---

## Writing your own connector

A connector is any program that speaks MCP over stdio. Two things matter for Grain: stdout carries the
protocol (log to stderr), and a tool's description and parameter schema are read by the model on every
turn, so keep them short and factual.

### Python

With the Python SDK (`mcp`):

```python
from mcp.server.mcpserver import MCPServer          # mcp 2.x; in mcp 1.x: from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

app = MCPServer("notes")
notes: list[str] = []

@app.tool()
def add_note(text: str) -> str:
    """Save a short note and return its number."""
    notes.append(text)
    return f"saved note {len(notes)}"

@app.tool(annotations=ToolAnnotations(read_only_hint=True))
def list_notes() -> str:
    """List every saved note."""
    return "\n".join(notes) or "no notes yet"

app.run()   # stdio
```

Type hints become the parameter schema and the docstring becomes the description.
[examples/mcp/python_notes](../examples/mcp/python_notes) is this server with its run instructions.

### TypeScript

With `@modelcontextprotocol/sdk` (the 1.x line) and `zod`:

```ts
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const server = new McpServer({ name: "hello", version: "0.1.0" });
server.registerTool(
  "greet",
  { description: "Greet someone by name.", inputSchema: { name: z.string() }, annotations: { readOnlyHint: true } },
  async ({ name }) => ({ content: [{ type: "text", text: `Hello, ${name}!` }] }),
);
await server.connect(new StdioServerTransport());
```

[examples/mcp/ts_hello](../examples/mcp/ts_hello) is a runnable version. The 2.x line of the TypeScript SDK
ships as separate packages (`@modelcontextprotocol/server`); the same shape applies, but check its docs.

### Add it in Grain

Library → Connectors → Add custom, transport `stdio`, then the command and arguments (use absolute
paths for a script, or set the working directory):

```
uv run /Users/you/code/notes/server.py
npx tsx index.ts        # with the working directory set to /Users/you/code/hello
```

Press Check. It starts the server, lists the tools and runs the static pass. Then ask a chat to use a
tool and approve the card. Annotations are shown on the card as "the server says", never as fact.

---

## Migration candidates

Grain has native integrations that overlap with connectors. This is what could move and what should not.

| Integration | Plan |
|---|---|
| Web fetch / search (`firecrawl.py`) | Candidate: replace with the official Firecrawl MCP server. Today a key in Settings makes it the primary backend for `web_search` and `fetch_url`; as a connector its tools would ask by default, so web lookups would need a grant to run unattended. |
| GitHub (`github_search`, `github_read`) | Candidate: replace with the official GitHub MCP server, which is a far larger surface (issues, pull requests, actions). Needs deferred search, which is already in place, and a read-only token recommendation. |
| Google (Mail, Calendar, Drive, Docs, Sheets, Tasks) | Keep native for now. OAuth scopes, the read cache, write verification and undo live in `google.py`, and a connector would bypass them. |
| Microsoft (Outlook Mail, Calendar) | Keep native for now, for the same reasons (`microsoft*.py`). |
| Telegram texting (`telegram.py`) | Stays native. It long-polls its own bot and routes messages into chat runs and approvals; it is a channel into Grain, not a tool Grain calls. |
| Health (COROS, Garmin) | Already connectors: `health_sync.py` creates and drives these servers through the same `McpServers` store. |
| Coding agents (`coding_session_*`, `opencode_run`) | Stay native. They own process lifetime, worktrees and approvals that a tool call cannot express. |

The test for moving something is whether the native code does anything the connector layer does not:
verifying a write, scoping a credential, caching, or showing an approval card with real context. If it
does, keep it native.
