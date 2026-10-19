# Coding agents

Grain does not edit code in a git repository itself. It hands the change to an external coding agent (a harness), checks the result, and reports back.

## Harnesses

| id | Harness | Notes |
| --- | --- | --- |
| `claude` | Claude Code | Runs outside the OS sandbox with the user's own account; asks inside its own session (status `needs_you`): a question can be answered with `coding_session_send`, a permission only by the user with `claude attach <id>`. |
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
| `coding_session_summary` | Cheap look: status, ids, last activity, and when `needs_you` what it waits on (`waiting_on`: permission or question). No log tail. |
| `coding_session_send` | Follow-up to a session that is done, failed or stopped (same id, same worktree and context), or the answer to a Claude Code session that is `needs_you` (it is stopped and resumed). Never approves a permission prompt. OpenCode and pi never wait for an answer. Not accepted while working. |
| `coding_session_stop` | Stop a running session. Files are kept; it can be continued with send. |
| `coding_session_diff` | What the session changed: status, stat, new files, commits; `full=true` adds the patch. |

Every tool takes the Grain id, the harness's `external_id` or its `session_id`. A failed delivery returns why and what to do.

## Worktrees

One live session per worktree: `coding_session_start` with `new_worktree=false` and `coding_session_send` are refused while another session is starting, working or `needs_you` there. The error names it; wait for it, answer it, stop it, or use `new_worktree=true`.

On a repo's main checkout every session gets its own branch and git worktree under `<repo>/.claude/worktrees` unless `new_worktree=false`. The checkout stays untouched and the branch can be reviewed and pushed on its own.

## Permissions

- Starting and following up ask the user in Manual mode, and whenever the run read untrusted content. An unattended background run can only propose them.
- A chat reply to a waiting Claude Code session is a message, not an approval: the pending permission is not granted. The user approves it in the session (`claude attach <id>`) or restarts it with a `permission_mode`.
- `permission_mode` (Claude Code only) overrides the session's mode; `bypassPermissions` under Auto or Manual always asks.
- With "Route coding to a coding agent" on (key `codingRoute`), Grain's own file writers and file-changing shell commands are refused inside a git work tree and the agent is told to start a session instead.

## Live activity and notifications

The Coding agents panel shows each session with a live timeline, a follow-up box, and stop and diff actions. The OS notifies when a session finishes, fails, or needs the user.

Structured events are stored per session and served at `GET /coding-sessions/{id}/events`; new ones are pushed on the SSE stream as `coding_session_event`.

## The coding extension

Coding is a built-in extension, on by default (Settings, Sidebar). Turning it off hides its tools, its Terminal card, its routes and the rule that sends repo edits to a coding agent. Its code is in `backend/personal_os/extensions/coding/` and `src/renderer/src/extensions/coding/`.

## Terminals an agent drives

`terminal_open`, `terminal_send`, `terminal_read`, `terminal_wait_for` and `terminal_close` give the agent a shell it can type into and watch: a dev server, a REPL, a long build. They are offered only while the desktop app is running and the OS sandbox exists (macOS).

- The shell is the user's login shell under the same OS sandbox and network choice as `shell_run`. The backend builds the command; the desktop app only runs it.
- A terminal belongs to the agent that opened it. Another chat, a worker, a workflow step or a job gets "No such terminal". At most 3 are open per agent and 6 for all agents; one idle for 30 minutes is closed; deleting the chat or ending the worker that owns it closes it.
- `terminal_send` takes one line (`text`, at most 4 KB, run at once) or one `key` (`ctrl-c`, `ctrl-d`, `escape`, the arrows, `tab`). A line is judged like a `shell_run` command: the hardline list, the user's allow, ask and deny rules, the destructive-command card, Grain's own folder, and the coding rule. Inside a git work tree a line that starts an interpreter, shell, editor or pager is refused, because what it does cannot be judged. A reply that read untrusted content while the shell can reach out asks first.
- `terminal_read` returns the new output since a cursor, or the screen. Control sequences are stripped, credentials scrubbed, and at most 16 KB (the newest) comes back, with how much was dropped. Output is untrusted text: reading it marks the reply as tainted.
- `terminal_wait_for` waits for a literal or a simple pattern, for the process to exit, for the output to go quiet, or for what looks like a prompt (a guess, labelled so). `timeout_s` is required.
- The Terminal card lists the agent's terminals. Clicking one opens it in the large window; while it is open the agent can read it but not type, and once you have typed the agent must send `ctrl-c` before it can type again.

## Recipes

A recipe is a saved coding task: a prompt with inputs, a repository, the driver, model and permission mode, tool lists and step and time limits (Claude Code), and a verify command. A run is done only when the verify command exits 0, whatever the agent says; with `max_retries` 1 the agent is told the scrubbed tail of a failed verify once and verify runs once more.

- Tools: `coding_recipe_list`, `coding_recipe_show`, `coding_recipe_save`, `coding_recipe_run`, `coding_recipe_status`. A version never changes: a save adds the next one. A version the assistant saves cannot run until you approve it in Settings, Coding recipes, because a recipe carries permission settings.
- `coding_recipe_run` asks, and its card shows the exact settings the run will use; the same settings are recorded with the run before the agent starts. Starting a run approves its verify command, which still cannot run if a deny or ask rule, the hardline list or the destructive-command card stops it (the run ends `verify_blocked`).
- Run statuses: `working`, `verifying`, `passed`, `failed`, `agent_failed`, `timeout`, `stopped`, `verify_blocked`, and for a job that found every session slot taken `queued_timeout` after 10 minutes.
- From the palette, "Run coding recipe..." opens the recipes panel. As a job: kind `coding_recipe`, with the prompt `{"recipe": name, "version": n, "inputs": {...}, "cron": "0 3 * * *"}`; the job pins the version.

## Structured runs

`coding_run_structured` runs Claude Code once, non-interactively, and returns a value that fits a JSON Schema (`schema`, at most 16 KB, no references outside it). It reads only by default; naming `Edit`, `Write`, `Bash` or `WebFetch` lets it write or run things, and in a git work tree that is refused while coding is routed to sessions. The answer is an error (`run_failed`, `agent_error`, `step_cap`, `no_structured_output`, `schema_mismatch`) unless it fits the schema; it is never guessed from the text form. The default budget comes from `ext.coding.structuredBudgetUsd` (1 USD when unset).

## Seeing what Claude Code does

Extensions can gate and observe the tool calls of a Claude Code session Grain started: `coding.tool.pre` (an extension may answer deny or ask, never allow) and `coding.tool.post`. They need `coding:read`, and `hooks:gate` for the gate. A session reports through a per-session key that opens only its own two routes while it is live. When nothing subscribes and no step limit applies, the session's settings are exactly what they were before.

## Claude Code orchestration

The `claude-code` extension (off by default; turn it on under Settings, Extensions; it needs the Coding extension) runs Claude Code sessions that Grain drives itself, instead of handing them to the Claude daemon.

How it differs from the `coding_session_*` tools:

| | Coding sessions (`claude --bg`) | Claude Code orchestration |
| --- | --- | --- |
| Process | A background session of the Claude daemon, outlives the app. | One `claude -p --output-format stream-json --input-format stream-json` process per session, a child of the app. It ends with the app. |
| Progress | Files the CLI keeps under `~/.claude/jobs`, polled. | The process's own event stream, stored as the session's event log (capped at 1000 events) and pushed to the open windows. |
| Questions | `needs_you`; the user answers in the terminal (`claude attach`). | A permission request becomes an approval card in the chat that started the session. |
| Many at once | One session per call. | `claude_code_fleet`: several sessions, queued behind a limit, with cost totalled for the fleet. |
| Worktrees | `<repo>/.claude/worktrees/<name>` | Under a folder Grain owns (below). |

### Tools

| Tool | Use |
| --- | --- |
| `claude_code_run` | Start one session: `repo_path`, `prompt`, optional `name`, `model`, `max_turns`, `budget_usd`, `permission_mode`, `allowed_tools`, `json_schema`, `new_worktree` (default true), `branch`. Returns at once with an id. |
| `claude_code_fleet` | Start up to 20 sessions as one fleet (`tasks`, `max_parallel`). What does not fit under the limits is queued and starts as running sessions settle; the fleet wakes the chat once, when the last one settles. |
| `claude_code_status` | One session (optionally its last N events), or a list of recent sessions and fleets. |
| `claude_code_send` | Steer or extend a session over its open input. It works while the session is running and after a result, until the process exits. |
| `claude_code_stop` | Interrupt a session (SIGINT; after a few seconds SIGKILL). The worktree and branch stay. |
| `claude_code_result` | Result subtype, `structured_output` (when `json_schema` was given), final text, cost per model, turns, permission denials and the worktree's diff. |
| `claude_code_wait` | Block until sessions settle (`mode` all or any, `timeout`). |

Starting, steering and fleets are external actions: a chat asks first, and an unattended run (a scheduled job) cannot use them. Reading a session taints the run, because what the agent wrote comes back through it.

### Approvals

A session's `--settings` carries Grain's floor from the coding extension (deny rules on Grain's own data and disk wipes, and the Bash hook that judges what a command runs) and a `PermissionRequest` hook. Claude Code runs that hook only when a call would otherwise prompt. The hook posts the request to a loopback route that opens with the session's own key, and waits for the user's answer for at most 10 minutes. The answer is an approval card in the chat that started the session; Allow everything and the auto reviewer apply to it as they do to any other card. A timeout, a late or malformed answer, an unreachable app or a missing card are all denials, and the denial tells the agent why.

A card needs the reply that started the session to be running: it is raised through that reply. The extension takes the card channel again whenever a later reply drives the session (`claude_code_send`, `claude_code_wait`, `claude_code_status`, `claude_code_result`). With no running reply (it ended, or the run was an unattended job) a request is denied with "no card to ask on". So stay in the reply with `claude_code_wait` while a session may ask, or start it with `permission_mode: "acceptEdits"` for edit-only tasks.

`permission_mode` is `default`, `acceptEdits`, `plan`, `auto`, `dontAsk` or `bypassPermissions`. `bypassPermissions` always asks the user first unless Grain itself is in Allow everything, and only in that case is the floor lifted, as for coding sessions.

### Worktrees

With `new_worktree` (the default) each session gets `git worktree add -b <branch> <root>/<repo>/<task>` with the branch `grain/<task>` (a short suffix is added when that name or path is taken). The root is the setting `claudeCodeWorktreeRoot`, `~/Grain/worktrees` by default. A root that is, or sits inside, a `.claude/worktrees` folder is refused, as is one inside Grain's own data folder: Claude Code cleans those up while sessions run. With `new_worktree=false` the session works in the repo itself, and only one live session may use a folder.

### Limits and cost

- `claudeCodeMaxConcurrent` (default 3) live sessions of this extension, and together with coding sessions never more than `codingSessionMaxConcurrent`. A finished session whose process is still open holds no slot.
- `max_turns`, `budget_usd` and the settings `claudeCodeMaxTurns`, `claudeCodeBudgetUsd` (0 means none) are passed to the CLI as `--max-turns` and `--max-budget-usd`.
- A result's `total_cost_usd` and `modelUsage` are cumulative for the session, so a session's cost is its latest result, and a fleet's is the sum of its sessions' latest, with a per-model breakdown.
- A session that has produced a result keeps its input open for five minutes, then the process exits and `claude_code_send` reports that it is gone.
- Sessions stop with the reply or subagent that started them when that run is stopped (a run that finishes normally leaves them working), when Coding or Claude Code is turned off, and when the app quits.

### Settings

`claudeCodeMaxConcurrent`, `claudeCodeDefaultModel` (empty uses the CLI's default), `claudeCodeDefaultPermissionMode` (never `bypassPermissions`), `claudeCodeBudgetUsd`, `claudeCodeMaxTurns`, `claudeCodeWorktreeRoot`. They are stored as `ext.claude-code.<name>`.

### Routes and events

Mounted at `/claude-code`: `GET /sessions`, `GET /sessions/{id}`, `GET /sessions/{id}/events?after=<seq>`, `POST /sessions/{id}/send`, `POST /sessions/{id}/stop`, `GET /fleets`. The open windows receive `session` (the whole session on every change) and `event` (one stored event) as `ext:claude-code:session` and `ext:claude-code:event`. Each finished tool call is also reported on the `coding.tool.post` hook, with the agent `claude`.
