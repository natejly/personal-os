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
| `tools` | Global mode per tool: on, ask or off |
| `alwaysAsk` | External and scheduling tools that show a card in Auto and Manual, not in Allow everything |
| `permissionRules` | `{allow, ask, deny}` lists of `Tool(pattern)` rules; deny beats ask beats allow |
| `skipPermissions` | Chats skip ordinary cards (a chat's own switch wins) |
| `unattendedApprovals` | A job run that would ask: `deny` (refuse) or `ask` (park a card) |
| `autoReview`, `autoReviewModel` | Review gate: a second model looks at a call before it runs unasked |
| `fetchAllowlist` | Hosts `fetch_url` may read after a reply touched untrusted content |
| `browserAllowlist` | Hosts the agent's browser may open by typed URL after untrusted content (it also honours `fetchAllowlist`) |
| `shellAllowedDomains` | Hosts the shell's and sandbox's egress proxy let through, beside package registries |
| `docEditMode` | `review` (diff to accept) or `apply` |
| `workspaceRoots` | Folders file tools write in without asking |
| `sandboxNetwork`, `sandboxImage`, `sandboxRuntime` | The Linux sandbox containers |
| `shellNetwork`, `shellRegistryAccess` | Network for shell commands |
| `deskShellAuto`, `deskDoneGate`, `deskSelfReview` | What a desk may do unasked, and what it must pass to finish |
| `browserEnabled` | Whether desks get a browser |

The three host lists stay separate fields because they gate different tools.

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

A tool's mode resolves chat, then agent, then project, then global, then the tool's default. A deny rule refuses over
any of them, and an Always ask tool tops out at ask whatever a map says.

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

The composer's skip-permissions switch sets the chat's own value and say when they follow the default.
Settings > Autonomy keeps the desk limits that are not permissions (turns, parking, vision, work environment).
