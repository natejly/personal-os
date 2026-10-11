# Coding agents

Grain does not edit code in a git repository itself. It hands the change to an external coding agent (a harness), checks the result, and reports back.

## Harnesses

| id | Harness | Notes |
| --- | --- | --- |
| `claude` | Claude Code | Runs outside the OS sandbox with the user's own account; asks for permission inside its own session (status `needs_you`, the user answers with `claude attach <id>`). |
| `opencode` | OpenCode | Runs inside the OS sandbox with every permission allowed. |
| `pi` | pi | Runs inside the OS sandbox; has no prompts of its own, so the sandbox is the only limit. |

A harness counts as installed when its binary is found on this Mac.

## Default harness

Settings, Advanced, Coding sessions, "Coding agent" (key `codingAgent`): `any` (default), `claude`, `opencode` or `pi`.

- A named value is the default harness.
- `any` means the first installed harness in the order claude, opencode, pi.
- `coding_session_start` takes `agent` as an optional argument. Omit it to use the default; Grain's agent passes it only when the user asks for a specific harness.
- An explicit `agent` that is not installed, or a default that is not installed, returns an error that lists the installed harnesses.

## Tools

| Tool | Use |
| --- | --- |
| `coding_session_start` | Start a session on a repo. Returns at once with an id. Give a self-contained brief; the agent cannot see the conversation. |
| `coding_session_list` | Recent sessions plus `harnesses: [{id, name, installed, default}]`. |
| `coding_session_wait` | Block until sessions finish, fail, stop or need the user (`mode` all or any, `timeout_s`). Prefer it over polling. |
| `coding_session_status` | One non-blocking look: status, current step, log tail. `events=N` adds the last N structured events. |
| `coding_session_send` | Follow-up to a session that is done, failed or stopped. Not accepted while it is working. |
| `coding_session_stop` | Stop a running session. Files are kept; it can be continued with send. |
| `coding_session_diff` | What the session changed: status, stat, new files, commits; `full=true` adds the patch. |

## Worktrees

On a repo's main checkout every session gets its own branch and git worktree under `<repo>/.claude/worktrees` unless `new_worktree=false`. The checkout stays untouched and the branch can be reviewed and pushed on its own.

## Permissions

- Starting and following up ask the user in Manual mode, and whenever the run read untrusted content. An unattended background run can only propose them.
- `permission_mode` (Claude Code only) overrides the session's mode; `bypassPermissions` under Auto or Manual always asks.
- With "Route coding to a coding agent" on (key `codingRoute`), Grain's own file writers and file-changing shell commands are refused inside a git work tree and the agent is told to start a session instead.

## Live activity and notifications

The Coding agents panel shows each session with a live timeline, a follow-up box, and stop and diff actions. The OS notifies when a session finishes, fails, or needs the user.

Structured events are stored per session and served at `GET /coding-sessions/{id}/events`; new ones are pushed on the SSE stream as `coding_session_event`.
