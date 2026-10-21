# Permissions

Everything that decides whether the assistant acts, asks first or is refused lives in one store and is set on one
Settings tab.

## The store

`backend/personal_os/permissions.py` owns a single settings row, `permissions`:

```json
{"version": 1, "tools": {"web_search": "off"}, "alwaysAsk": ["gmail_send"]}
```

It holds only what the user changed; `permissions.DEFAULTS` fills in the rest on read. The keys:

| Key | What it decides |
| --- | --- |
| `permissionMode` | `auto` (a reviewer model reads every call that is not known safe), `manual` (tool modes, grants and rules alone) or `allow_all` (no cards; see below) |
| `tools` | Global mode per tool: on, ask or off |
| `alwaysAsk` | External and scheduling tools that show a card in Auto and Manual, not in Allow everything |
| `permissionRules` | `{allow, ask, deny}` lists of `Tool(pattern)` rules; deny beats ask beats allow |
| `unattendedApprovals` | Manual mode only: a job run that would ask is refused (`deny`) or parks a card (`ask`) |
| `autoReviewModel` | The model Auto mode's reviewer uses ("" = the fast model) |
| `allowAllConnections` | Host lists admit any public hostname and MCP tools run without a per-tool grant |
| `trustExternalContent` | Lifts the two taint-forced cards (a networked shell command, a write outside the desk) after untrusted content |
| `codingRoute`, `codingAgent` | Repo edits go to a coding agent session, and which agent it starts by default |
| `fetchAllowlist` | Hosts `fetch_url` may read after a reply touched untrusted content |
| `browserAllowlist` | Hosts the agent's browser may open by typed URL after untrusted content (it also honours `fetchAllowlist`) |
| `shellAllowedDomains` | Hosts the shell's and sandbox's egress proxy let through, beside package registries |
| `docEditMode` | `review` (diff to accept) or `apply` |
| `shellNetwork`, `shellRegistryAccess` | Network for shell commands |
| `deskShellAuto`, `deskDoneGate`, `deskSelfReview` | What a desk may do unasked, and what it must pass to finish |
| `browserEnabled` | Whether desks get a browser |

The three host lists stay separate fields because they gate different tools.

Kept only so stored values load and old clients can still send them: `skipPermissions` (global and per chat) and
`autoReview`. No gate reads either; `permissionMode` decides.

API: `permissions.get(cfg, key)` reads one value from a settings dict (with its default); `load(rows)` returns all of
them; `save(db, patch)` writes validated values; `update(db, fn)` is a read-modify-write under one write lock, used by
approval cards that add to a map or list; `validate(key, value)` is what `PUT /settings` runs.

Compatibility: `app.settings()` flattens the store back to the top level of the settings dict, and `GET /settings`
returns both the flattened keys and the nested `permissions` object. `PUT /settings` accepts the keys top-level (as
the renderer sends them) or nested under `permissions`; a top-level key wins. A top-level row written by an old code
path wins over the store for its key until the next save folds it in.

## The migration

Migration 5 (`migrations._permissions_store`) folds every legacy top-level permission row into the `permissions` row
and deletes it. A fresh database gets `{"version": 1}`. Gates read the same values before and after.

## Scopes

Narrower scopes stay where they are, because they are scopes, not copies:

- `conversations.settings`: `tools`, `skipPermissions`, `workingFolder`
- `agent_defs.tool_modes`, `projects.tools`
- `mcp_grants` (connector grants, bound to the schema they approved)
- the in-memory session grants (`permrules.SESSION`, "allow for this chat session")
- `desks.autonomy` and `jobs.desk_autonomy`, `jobs.allowed_tools` (these only narrow)

A project (`projects.permissions`) and a chat (`conversations.settings.permissions`) may also override five keys:
`permissionMode`, `permissionRules`, `alwaysAsk`, `destructiveDefaults` and `unattendedApprovals`. During a turn
`settings()` overlays them (`permissions.overlay`), so every gate that reads `permissions.get(cfg, key)` sees the value in
force for that chat. The mode, destructive defaults and unattended setting replace the wider value; rules and Always ask
entries are added to the wider scope's, so a narrower scope cannot remove a global deny. Network, host-list and trust
keys are global only. A chat settings PATCH cannot set a permission key.

A tool's mode resolves chat, then agent, then project, then global, then the tool's default. A deny rule refuses over
any of them, and an Always ask tool tops out at ask whatever a map says.

## Core guards

`guards.py` holds the refusals that need no card: an unknown or switched-off tool, a proposal-only run, plan mode, the
hardline list, deny rules, Grain's protected paths and a destructive call Manual mode blocks. They run in a fixed order,
first in `dispatch_tool` (before any `tool.pre` handler), again when a loop settles a call on run or card, and in
`RunStore.open_approval`, which refuses to open a card for a call they refuse. The extension hook `tool.permission` runs
only after them and can only narrow (deny, or turn a run into a card). No extension is handed the guards, and `guards.sealed`
checks at start and on every call that they are unchanged; if not, every call is refused.

## Effective configuration

`GET /config/effective?project_id=&conversation_id=`, `grain config dump [--json] [--project ID] [--chat ID] [--data-dir PATH]`
and Settings > Advanced > Effective configuration show every setting with its source (`default`, `ext:<id>`, `user`, `project`, `chat`), the values it shadows, each tool's mode and where that mode was set, and whether
each secret is set (never more). The CLI opens the database read-only and needs no server.

## Allow everything

`permissionMode: allow_all` runs tools without cards or a reviewer. That includes unsandboxed shell commands, moving
or trashing local files, running Shortcuts, installing Python packages, scheduling tasks and deleting calendar
events. Nothing asks: not a delete outside the Trash, a force-push, a disk wipe, a credential or Grain-data path,
a write after untrusted content, a runaway repeated call, sending email, or a Diff-review doc edit (it is written
straight into the file; the file's history undoes it). Only these still hold: deny rules refuse, the hard-deny paths
(Grain's own data folder and app) stay refused, a plan or a question to the user is still a question, and a background
run with nobody present still cannot wait on a card.

Every action is still logged in approval history, and an unsandboxed shell command also gets a backend log line. A
coding session started in this mode runs with its own CLI floor lifted too (only the denies on Grain's data and disk
wipes remain).

## Shell sandbox

`shell_run` states its limits in its tool description: no binding or listening on a port, no Apple events or launching
apps, no writes to Grain's data or app, credential stores, rc files, git hooks or launch agents. A command that needs
one passes `unsandboxed=true` for that one call: an approval card in Auto and Manual, none in Allow everything. A
sandboxed command that hits a wall returns `sandbox_blocked` with the quoted OS error, the sandbox rule and why.

## The surface

Settings > Permissions, in order: tool access, Always ask, rules and the rule tester, grants (global modes, allow
rules, connector, session, chat, agent and project grants, chats that skip permissions, each with a revoke), run
safety, folders, shell and sandbox network, browser, desks, skip permissions, file edit mode.

Approval cards write into the same store: "Always" (`always_global`) adds to `tools`, "Always allow this pattern"
(`always_rule`) adds to `permissionRules.allow`, "Allow this host" (`allow_host`) adds to `fetchAllowlist`.

Settings > Autonomy keeps the desk limits that are not permissions (turns, parking, vision, work environment).
