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
| `alwaysAsk` | External and scheduling tools that show a card in Auto and Manual, not in Allow everything (sending email is the exception: it always shows the review card) |
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
events. What still asks or is refused:

- Deny rules refuse.
- Grain's own data folder and app bundle, and credential stores (keys, passwords, sign-in files, Keychain).
- Permanent deletes outside the Trash (`rm` of files outside a temp folder, `shred`, `find -delete`, `xargs rm`), disk
  wipes (`diskutil erase`, `dd` to a disk device, `mkfs`) and a git force-push: an approval card.
- Writes right after untrusted content, and a runaway repeated call: a card.
- Sending email: always the editable review card.

Every action is still logged in approval history, and an unsandboxed shell command also gets a backend log line.

The shell floor is `permrules.allow_all_floor()`, called from the chat loop, the `run_python` tool bridge and workers
through `shell.floor()`. `destructive()` parses chained commands, pipes, wrappers such as `sudo`/`env`/`nohup`, `$()`,
backticks, `sh -c` and `eval` strings and, best effort, subshells and `{ }` groups; it follows a literal `cd` through
the chain. Paths under `/tmp`, `/private/tmp`, `/var/folders`, `$TMPDIR` and a desk's `work/` folder are scratch, not
user files. For an unsandboxed command `touches_protected()` also cards any path-like word that names a credential
store or Grain's own data or app, and the Keychain CLI (`security find-…`, `dump-keychain`, `export`). Sandboxed
commands get that protection from the OS sandbox. The floor card has no session or rule option.

## The surface

Settings > Permissions, in order: tool access, Always ask, rules and the rule tester, grants (global modes, allow
rules, connector, session, chat, agent and project grants, chats that skip permissions, each with a revoke), run
safety, folders, shell and sandbox network, browser, desks, skip permissions, file edit mode.

Approval cards write into the same store: "Always" (`always_global`) adds to `tools`, "Always allow this pattern"
(`always_rule`) adds to `permissionRules.allow`, "Allow this host" (`allow_host`) adds to `fetchAllowlist`.

The composer's skip-permissions switch sets the chat's own value and say when they follow the default.
Settings > Autonomy keeps the desk limits that are not permissions (turns, parking, vision, work environment).
