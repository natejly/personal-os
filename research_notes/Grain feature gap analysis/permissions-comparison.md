# Grain permissions compared with leading apps

As of 2026-10-02. Synthesized only from [permissions-app.md](permissions-app.md) and [permissions-market.md](permissions-market.md). No further research: the two notes do not disagree on a shared claim. Contradictions inside each note are labeled below.

Grain here is the working tree, not HEAD and not the older design notes. The app note’s rule is that code on disk wins. The uncommitted skip-permissions control is current behavior.

## 1. What Grain ships now

From the app note only.

### Tool modes

Built-in tools resolve `on | ask | off`. `ToolOverride` adds `inherit`. Stored booleans still normalize (`true` → `on`, `false` → `off`). Resolution order is chat override, then project override, then the global map, then a danger-tier default. A missing or invalid value does not override. `inherit` does not normalize, so it falls through.

Danger defaults: `safe`, `writes`, `network`, and `executes` start at `on`. `external`, `plan`, and `schedules` start at `ask`. A spec may override that default. `shell_run` is in `executes`, and its spec default is `ask`.

If the conversation’s `useTools` is false (default true), the chat loop loads an empty mode map and a call’s raw mode is `off`.

MCP tools use the same three scopes in a separate `mcp_grants` table, unique on `(tool_slug, scope, scope_id)`, bound to `schema_hash`. No grant means `ask`. A later scope replaces an earlier one. If the grant’s hash differs from the live tool, `on` becomes `ask`. A quarantined (drifted) MCP tool is not offered. A slug whose server is not ready is left out. A chat tool map entry of `off` removes that slug even if a grant says `on`.

Chat per-tool overrides are in the context drawer, against a project base. Global toggles and the rules editor are on Settings → Tools.

`llm.py` still comments that a missing tool key means `on`. `Toolbox.effective` does not do that. A missing key uses the danger default, so `external` stays `ask`. That is a stale comment inside the app note, not two behaviors.

### Rules, skip-permissions, and approval cards

Rules are one global allow / ask / deny list of `Tool` or `Tool(pattern)` strings, stored at `settings.permissionRules`, default empty lists. They are not per project or per chat. Malformed entries are skipped.

Subjects:

- `Bash(<command>)` for `shell_run`
- `Read(<path>)` for `read_local_file`, `fs_glob`, `fs_grep`
- `Edit(<path>)` for `write_local_file`, `fs_edit`, `fs_copy`, `fs_mkdir`, `move_local_file`, `trash_local_file` (copy and move also see source and dest)
- `Agent(<type>)` for `agent_spawn`
- otherwise the bare tool name, with no argument

Shell evaluation order inside `_evaluate_bash`: hardline deny, then any deny rule, then ask if any subcommand hits an ask rule or the line is opaque or a path is outside the workspace roots, then allow only if every subcommand is read-only or allow-listed, else no opinion. Deny and ask see nested `$(...)` and `sh -c`. One denied or asked part decides the line.

Hardline refusals cannot be lifted by a rule or a card. The app note names the categories (fork bomb, recursive removal of root, home, or system directories, filesystem creation, disk-device writes, and a fixed set of shutdown and erase commands). This comparison does not restate command strings.

The fixed read-only shell set includes `ls`, `cat`, `head`, `tail`, `wc`, `pwd`, `echo`, `which`, `file`, `stat`, `grep`, `rg`, `find` (find writers such as `-exec` and `-delete` are excluded), and `git status|diff|log|show|branch` with a small flag set.

`resolve` behavior:

- Incoming `off` is returned unchanged. Rules do not apply.
- A deny sets `refusal` and does not change mode. The loop emits `tools.denied` before the tool runs.
- A doom-loop forces `ask` and `forced` even after an allow.
- An ask rule sets `ask` unless a session grant covers the pending keys and the card is not forced.
- An allow rule, or a session grant with no rule opinion, turns `ask` into `on` only when the card is not forced.

The loop does not call `resolve` for MCP slugs. The comment in `app.py` says MCP keeps schema-bound grants.

Session grants live in memory, per conversation id, for the exact set of pending keys, and clear on process restart. `always_session` adds them only when the card was not forced.

The third identical executed call in a row (`StuckDetector.repeat_count >= 2`, `DOOM_LIMIT`) becomes a forced `doom_loop` card. Rules do not lift it. Skip-permissions does, because a doom-loop card is an ask and the lift does not check `forced`.

Three consecutive refusals (rule deny, unattended deny, or a denied card) make `DenialStreak` attach a stop-varying note. The loop calls `note()` before `record()`, so the note appears once the counter is already at 3, on the following result.

Skip-permissions (`skip_permissions_on`): if the conversation settings dict contains `skipPermissions`, that boolean wins, including false; otherwise the global setting, default false. The chat loop clears the flag when `run.kind` is `job` or `scheduled`. `lift_permission_ask` turns a remaining `ask` into `on` except for `propose_plan` and `desk_ask` (`STILL_ASK`). `off` stays `off`. A refusal already in `pre` is not replaced. The lift runs after plan mode, desk autonomy, taint, and `resolve`, and it does not check `forced`.

Inference from those predicates: skip-permissions lifts ordinary asks, doom-loop cards, taint-forced asks, desk “ask as it goes” cards, and outside-folder file cards. It does not lift `off`, a refusal, plan-drafting blocks, propose-only external blocks, `propose_plan`, or `desk_ask`. The lift predicate does not name an MCP exception. The MCP exception is stated for `resolve` only.

The composer button writes the chat setting when a conversation id exists, otherwise the global setting. Displayed state is the chat value if present, else global. Settings → Tools is the global default. Its copy says deny rules still refuse, plans and desk questions still wait, and jobs keep their own setting. The context drawer says cards are off and deny rules still refuse when the effective flag is on.

Contradiction inside the app note, no extra research: that Settings sentence says jobs keep their own skip setting, and the chat loop clears the flag for `job` and `scheduled` runs. Code-on-disk is the behavior the app note tells a comparison to trust. Jobs and scheduled runs do not keep skip turned on.

A `run_python` tool-bridge call returns approved immediately when skip is on, without a card (`_bridge_approve`).

`POST /approvals/{call_id}` accepts `allow`, `deny`, `always_chat`, `always_global`, `always_session`, and `always_rule`. The first decision wins on the SQLite row. The chat wait loop has no auto-deny timer.

`always_rule` appends validated rules to the global allow list only if the row is pending, not forced, and not a plan. Saved rules must match a subject of this call, must have a pattern, and must not be a blanket `*` (max 5).

`always_chat` and `always_global` set that tool’s mode to `on` (MCP: a grant at that scope). If the card was forced, or the tool is `propose_plan`, the standing grant is dropped and the decision is a one-shot `allow`. The loop re-reads settings before writing.

Card extras, when the call is still asking: kind, subject, rule, suggestions, and whether a session grant is offered. Forced cards omit session and saved-rule buttons. Kinds the UI explains: `doom_loop`, `opaque`, `external_directory`, `rule`. Optional edited arguments on a non-plan card are schema-checked and become the call that runs. Parked cards reject edits. A deny can carry a note back to the model (500 characters on the live path).

`POST /permissions/evaluate` previews a rule or a call against saved rules and roots. It does not run the tool.

Unattended jobs: if the call would still ask and `unattendedApprovals` is `deny`, the loop records a denied approval and refuses. The default of `unattendedApprovals` is `ask`. The app note does not say what the default `ask` value does with a card on a job or scheduled run. A background proposal-only run that still needs a card is refused rather than parked.

Writes outside granted folders, or inside a granted folder that is not a desk workspace after untrusted content, set `fs_needs_ask`. That upgrades `on` to `ask` and marks the card forced, so an allow rule cannot turn it back to `on`. Desk-workspace paths do not ask for this reason. Skip-permissions can still lift the forced ask.

Taint (sticky on the conversation, plus this turn’s meetings, activity, document excerpts, page detail, or a sandbox import) forces `external`, `network`, `schedules`, prompt-write tools, and a networked sandbox from `on` to `ask`. That upgrade is forced, so rules do not downgrade it. Skip-permissions still lifts it.

`shell_run` with `unsandboxed: true` is `force_ask`. A sandboxed shell whose cwd is inside the desk workspace can skip its default `ask` when `deskShellAuto` is on (default true) and the user has not set a shell mode. Later gates still apply.

Absent, not partial: per-project or per-chat rule lists, a grant audit (who, when, how many fires), and a batch card for several asks in one round.

### What is verified on external writes

Google Calendar, Gmail, Tasks, Docs, and Sheets writes that go through `google.py` are re-read and labeled `verified`, `unverified`, or `mismatch`. Anything but `verified` becomes a tool error so the model is told not to retry. `verify.check` does not raise. Retries are 0.5s and 1.5s; mail adds 3.0s.

Shipped read-backs:

- `calendar_create` / `calendar_update`: fields actually written, plus status
- `calendar_delete`: absent or a cancelled tombstone
- `calendar_respond`: the self attendee’s `responseStatus`
- `gmail_send`: message id, `SENT` label, thread id, subject, and To addresses. Body is not compared
- `gmail_draft`: draft exists and subject matches. Recipients and body are not compared
- `gmail_modify`: labels
- `tasks_add`, `tasks_complete`, `tasks_insert`, `tasks_update`, `tasks_delete`
- `docs_create` / `docs_append`: title when set, and the written text as a whitespace-collapsed tail of at most 200 characters
- `sheets_write`: row shape and filled-cell count
- `sheets_create`: spreadsheet title only. Initial cell values are written and not compared

Held sends go out through `gmail_send`, so the same SENT read-back applies. `calendar_ensure` returns `{id, summary, created}` with no `verify.attach`.

Local files, the host shell, the browser, and MCP calls are not on this path. `post_write` records the file as read and, for `.py`, `.json`, `.toml`, and `.yaml`, reports `syntax_error` if the text does not parse. It does not compare bytes to what was requested. `pre_write` can require a full read before overwrite (`requireReadBeforeWrite`, default true). `filesnap` stores a pre-image for undo when wired. Sensitive local paths are refused (`.env`, keys, credential directories, `/dev`, `/proc`). That is a gate, not a verification. The app note did not re-audit `mac.allowed_path`’s full deny list. Cowork output promotion re-reads the destination and returns `verified` for accepted desk outputs, not for every tool write.

### Desk autonomy and parked cards

Desk autonomy is `plan`, `ask`, or `propose`. New desks default to `plan`. UI labels: “Plan first”, “Ask as it goes”, “Work and propose”. The loop reads `autonomy` from the desk row each turn. Ordinary chats do not use it. Chat plan mode is forced empty when a desk is set.

Chat plan mode is separate: `off | auto | always`, chat setting over global, default `off`. `always` starts drafting immediately. `auto` flips planning on at the first mutating call whose group is not exempt, refuses that call, and offers a reduced schema next round. The app note did not confirm `PLAN_AUTO_EXEMPT_GROUPS` membership.

While `planning` is true, any tool whose danger is not `safe`, `network`, or `plan` is set `off` with reason `PLAN_BLOCKED`. That is not a card. Desk `plan` starts in that state until an approved plan exists.

`propose`: if danger is `external`, mode is `off` and the reason is that this desk may only propose external actions. It does not write a proposal row. `writes` and `executes` are not in that branch. Background jobs are the path that records proposals instead of calling external tools (`proposal_only`).

`ask` autonomy: danger in `writes | executes | external` becomes a forced `ask`. A standing grant cannot clear it. Skip-permissions can.

`propose_plan` is always a card (`ask`, and not `forced` for grant purposes). An approved step whose argument digest matches is claimed once, except when the approval was forced (taint). A previously rejected step is denied. A chat consults the plan when it would have asked. A desk also claims steps that were going to run.

These cowork and plan branches only narrow: they set `off` or forced `ask`. Shell `auto_ok` can clear a default ask earlier, and these branches can put it back.

Parking: only if `desk_id` is set and `parkAfterSeconds` > 0 (default 180). Chats pass 0 and wait with no timeout. A desk parks when the run has zero watchers and the wait has reached the threshold. Status stays `pending`, `decided_by='park'`. The reply ends, the card stays, desk status becomes `blocked`. Answering a parked card does not resume the dead run. The approval route records the decision, patches the transcript, and wakes a later turn. An approved ordinary call must be repeated with the same argument digest and is spent once. A different digest asks again. Parked cards cannot take edited arguments. A parked yes is one digest, one desk, one use. It is not a session grant and not a saved rule.

Approvals are SQLite rows. Startup recovery can reattach pending cards onto a message. A live run is still woken by an in-memory future. A chat whose run died records the decision and tells the user the call did not run.

## 2. What leading apps ship that Grain does not

Named from the market note, with that note’s sources. Gemini CLI, GitHub Copilot’s coding agent, and Claude Code’s Cowork-tab modes were not in the fetched pages. This section does not cover them.

### Fenced skip, and a deny-unless-allowlisted unattended mode

Claude Code ships session modes Manual (`default`), `acceptEdits`, `plan`, `auto`, `dontAsk`, and `bypassPermissions`. `dontAsk` runs only pre-approved tools and auto-denies the rest. `bypassPermissions` skips prompts and still prompts for explicit ask rules, org-forced connector asks, tools marked `requiresUserInteraction`, and critical-path `rm` / `rmdir`. In a non-interactive bypass run, calls that would still have prompted are denied instead. The docs say to use auto mode, not bypass, when the goal is fewer prompts with background checks. Source: [Choose a permission mode](https://code.claude.com/docs/en/permission-modes), [Configure permissions](https://code.claude.com/docs/en/permissions).

Grain’s skip-permissions flag lifts remaining asks except `propose_plan` and `desk_ask`, including forced asks and ask-rule asks. It is not fenced the way `bypassPermissions` is. Grain’s `unattendedApprovals=deny` branch refuses a call that would still ask, and it is not the default. The default is `ask`, and the app note does not describe that default’s job behavior.

Codex’s documented non-interactive read-only combination is a read-only sandbox plus an approval policy of `never`. `never` disables prompts and still leaves the sandbox in force. Full access without prompts is labeled not recommended. Source: [Agent approvals & security (developers)](https://developers.openai.com/codex/agent-approvals-security).

### A classifier inside a boundary that still asks

Three products review some boundary crossings with a model after deterministic rules:

- Claude Code `auto` checks rules first, auto-approves read-only actions and working-directory edits except protected paths, and sends the rest to a classifier. A block is returned to the model. On entering auto mode, broad allows (`Bash(*)`, wildcarded interpreters, package-manager run commands, `Agent` allows, and others) are dropped for that mode. Source: [Choose a permission mode](https://code.claude.com/docs/en/permission-modes).
- Cursor Auto-review is the recommended default as of the 2026-05-29 note on the Run Modes page. Allowlisted calls run immediately. Other shell commands run in the sandbox when they can. Calls that cannot use the sandbox go to a classifier. File-Deletion Protection, External-File Protection, and Browser Protection can still require approval when a mode would otherwise run automatically. The page says the classifier is not a security boundary. Run Everything is described as accepting the risk for zero prompts. Source: [Run Modes](https://cursor.com/docs/agent/security/run-modes).
- Codex `approvals_reviewer` can be `auto_review`. The reviewer sees actions that already need approval. It can proceed on low and medium risk when policy allows, denies critical risk, and requires user authorization plus no matching deny for high risk. Failures fail closed. ChatGPT desktop’s “Approve for me” uses the same sandbox as “Ask for approval”; boundary crossings go to automatic review. “For most work, start with Ask for approval.” Source: [Agent approvals & security (developers)](https://developers.openai.com/codex/agent-approvals-security), [Permissions](https://learn.chatgpt.com/docs/permission-modes).

Grain has no classifier review step in the app note.

### Sandbox policy as its own control

Codex separates sandbox (`read-only`, `workspace-write`, `danger-full-access`) from approval policy. Writable roots still keep `.git`, `.agents`, and `.codex` read-only. Network is off unless workspace-write network access is enabled. When the network proxy is on, `deny` wins over `allow`, and a hostname that resolves to a private address stays blocked even if the name is allowlisted. Source: [Agent approvals & security (developers)](https://developers.openai.com/codex/agent-approvals-security), [Codex rules](https://developers.openai.com/codex/rules).

Cursor’s sandbox can read and write the workspace, blocks network until a network mode opens it, and cannot freely read a listed set of protected paths. `sandbox.json` gives `deny` priority over `allow`. Private addresses and the cloud metadata address are blocked by default. Team-admin policy cannot be weakened by the local file. Source: [Run Modes](https://cursor.com/docs/agent/security/run-modes), [sandbox.json reference](https://cursor.com/docs/reference/sandbox).

Grain’s app note shows a host `shell_run` under Seatbelt, a `shellNetwork` boolean in the README shell section, `deskShellAuto`, and `unsandboxed` forced to ask. It does not show user-facing sandbox modes separate from tool modes, and it does not show a host-level deny-wins network list. The app note did not re-verify the sandbox profile.

### Layered rules, workspace trust, and MCP under the same deny list

Claude Code evaluates deny, then ask, then allow. Specificity does not reorder that. A deny at any settings level cannot be allowed by another level. Settings layers are user, project, local, and managed. Project allow rules apply only after the workspace trust dialog. Deny and ask apply without that trust. A bare deny removes the tool from context; a scoped deny leaves it visible and blocks matching calls. Allow globs are rejected except after a literal MCP server prefix. MCP tools marked `requiresUserInteraction` prompt on every call and do not offer always-allow. In `dontAsk` those tools are denied even if an allow rule matches. A PreToolUse hook cannot override a deny or an ask. A hook that exits 2 can block before rules run. Source: [Configure permissions](https://code.claude.com/docs/en/permissions), [Choose a permission mode](https://code.claude.com/docs/en/permission-modes).

Codex prefix rules: if several match, the strictest wins (`forbidden` over `prompt` over `allow`). Project rules load only when that project layer is trusted. Admins can put restrictive rules in `requirements.toml`. Source: [Codex rules](https://developers.openai.com/codex/rules).

Cursor stores allowlists and plain-English Auto-review instructions in user and project `permissions.json`. A team dashboard configuration, when defined, takes priority and the local files are ignored. Those sentences steer a classifier. The reference page says an allow instruction still goes through the safety check, and a block instruction can still be approved. That is a weaker deny guarantee than Claude’s ordered scan or Codex’s strictest-match sort. Source: [Run Modes](https://cursor.com/docs/agent/security/run-modes), [permissions.json reference](https://cursor.com/docs/reference/permissions).

Grain already evaluates a deny before an allow inside one global list, including for shell. Grain does not ship project or chat rule lists, a managed layer, or a workspace-trust gate in front of allow rules. MCP slugs skip `resolve`, so a global deny rule does not name them. Chat `off` and an MCP grant of `off` can still silence a slug.

### Path rules that cover the edit, and protected paths

Claude Read and Edit rules use `//`, `~/`, settings-relative `/`, and `./`. A `Read` deny also blocks Edit and Write on that path. Those path rules also apply to recognized Bash file commands such as `cat` and `sed`, and not to a Python or Node script that opens files itself. Symlinks: an allow matches only if both the link and the target match; a deny matches if either matches. Source: [Configure permissions](https://code.claude.com/docs/en/permissions).

Protected paths (`.git`, `.claude` with a worktree exception, `.vscode`, `.idea`, shell rc files, `.npmrc`, `.mcp.json`, and others on that page) are never auto-approved except in `bypassPermissions` and in plan sessions where bypass is available. An allow rule does not pre-approve those writes. Critical-path removals cannot be approved by an allow rule or by a PreToolUse `"allow"`. In `bypassPermissions` those removals still ask. Source: [Choose a permission mode](https://code.claude.com/docs/en/permission-modes).

Grain’s file-tool sensitive list that was read is `.env`, keys, credential directories, `/dev`, and `/proc`, and those are refusals. `Read` and `Edit` are separate subjects. The app note does not say a `Read` deny is consulted for an edit tool. Inference: a `Read(<path>)` deny does not by itself refuse an `Edit` subject. The full `mac.allowed_path` list and symlink behavior were not re-audited, so this comparison does not claim `.git` is writable.

### Per-task permission mode for scheduled work

Claude Code Desktop scheduled tasks each have their own permission mode. “Always allow” on a Run now prompt is reused by later runs of that same task and can be revoked from an Always allowed panel. The stall-avoidance instruction is to Run now once and choose always allow, not to turn on bypass. Manual mode stalls until a person answers. Cloud routines have no permission prompts. The docs do not say where the per-task mode or the Always allowed set is stored. Source: [Schedule recurring tasks in Claude Code Desktop](https://code.claude.com/docs/en/desktop-scheduled-tasks).

Grain jobs and scheduled runs clear skip-permissions. They do not have a per-task mode in the app note. `unattendedApprovals` is a global setting.

### What this section does not treat as a missing Grain feature

- Mail and calendar confirmation copy, always-allow scope, and undo are not specified on any fetched help page. Leaders review those actions when they arrive as an MCP tool, a connector, a Computer Use app, or an SDK tool marked for approval. Source: [Configure permissions](https://code.claude.com/docs/en/permissions), [Computer Use](https://learn.chatgpt.com/docs/computer-use), [Human-in-the-loop (Python)](https://openai.github.io/openai-agents-python/human_in_the_loop/). Grain’s missing recipient-sized rule is a Grain limit (section 4), not a copied leader screen.
- ChatGPT Computer Use asks before using an app and can save an app allow list. The permissions notes do not describe Grain driving other desktop apps. That is not a close target.
- The Agents SDK ships a pause/resume primitive (`needs_approval`, sticky always-approve for the rest of one run). It is not a product mode switch. Hosted shell environments do not support `needs_approval`. Source: [Human-in-the-loop (Python)](https://openai.github.io/openai-agents-python/human_in_the_loop/).
- MCP itself defines no allow/ask/deny modes and stores no approvals. Annotations are untrusted hints. Defaults in the March 2026 blog: `readOnlyHint` false, `destructiveHint` true, `idempotentHint` false, `openWorldHint` true. Source: [MCP tools, 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools), [Tool annotations blog, 2026-03-16](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/).

Contradiction inside the market note, no extra research: the Codex developers page still describes `approval_policy = "untrusted"`. The ChatGPT Learn page says that value is retired and can prevent startup, and points at the 2026-08-20 change that removes it. Do not treat `untrusted` as either currently valid or fully gone. Source: [developers](https://developers.openai.com/codex/agent-approvals-security), [Learn](https://learn.chatgpt.com/docs/agent-approvals-security), [PR 39630](https://github.com/openai/codex/pull/39630).

Contradiction inside the market note, no extra research: the developers page says a destructive MCP annotation always requires approval even beside other hints. The Learn page says a read annotation takes priority over destructive. Do not treat “destructiveHint forces a prompt” as a single rule. Same two URLs.

## 3. Where Grain is already ahead, or different on purpose

### Ahead

Deny wins inside `resolve`, and a hardline shell subject is a refusal. Skip-permissions, session grants, allow rules, and whole-tool `on` do not lift a refusal. Claude’s bypass still asks for critical-path removals. Grain refuses the hardline set with no card.

Google writes through `google.py` are re-read and a non-verified result is a tool error that tells the model not to retry. The fetched leader pages do not describe a built-in read-back for mail, calendar, tasks, docs, or sheets. The comparison is incomplete on purpose: body text, draft recipients, sheet seed cells, and `calendar_ensure` are outside that proof, and local files, shell, browser, and MCP are not on `verify.py`.

An MCP `on` grant decays to `ask` when `schema_hash` changes, and a drifted tool is not offered. The market notes do not describe schema-hash grant decay.

Desk cards park (`parkAfterSeconds`, default 180) when nobody is watching, keep a pending SQLite row, and a later yes is honored once for the same argument digest. Interactive chats wait with no auto-deny timer. Startup recovery can reattach a pending card. A dead chat run does not execute the call.

Shell rules already split compound commands, see command substitution, ask on an opaque line, and ask when a path is outside workspace roots. Saved card rules require a pattern, reject a blanket `*`, and cap at 5. Forced cards omit the session and saved-rule buttons. `POST /permissions/evaluate` previews a decision without running the tool.

Taint is sticky on the conversation and forces `external`, `network`, `schedules`, prompt-write tools, and a networked sandbox back to a forced ask. Rules do not downgrade that. The gate is ahead of a pure tool-mode default. Skip-permissions currently lifts it, which is the first build below.

Sensitive paths in the list that was read are refused. `requireReadBeforeWrite` defaults true. Parsed text files get a syntax check after write. A pre-image can be kept for undo. Those are gates and recovery, not the Google-style read-back.

### Different on purpose

Interactive chats no longer auto-deny after a timeout. The app note says the working tree matches the “delete the 600 second auto-deny” change, and that desk parking is a timed release of the run rather than a deny. Leave the chat wait as it is. Do not add a blanket timeout that denies a card the user can still see.

Desk `plan` / `ask` / `propose` is a desk row, applied after tool modes and before permission rules. Chat plan mode is forced empty on a desk so the two do not stack. “Work and propose” refuses `external` danger. It does not turn every action into a proposal row. Local writes follow normal modes, rules, and folder grants.

The writes tier defaults to `on`, and `fs_needs_ask` forces a card outside roots, or inside a non-desk root after untrusted content. That is the workspace model. It is not a missing “accept edits” mode. Desk-workspace paths do not take that card even when the conversation is tainted. That desk exception is what the code does relative to the older cowork-3 note. Builds below do not put a card on every in-desk write.

MCP allows stay on schema-bound grants. The part that is not justified by that comment is the missing deny: a global deny rule never sees the slug. Section 4 treats that as a gap.

Skip-permissions is already cleared for `job` and `scheduled` runs in the loop. That is the right unattended fence. The Settings sentence that says jobs keep their own skip setting does not match the loop. Do not “fix” the loop by letting jobs inherit skip.

## 4. Gap table

Historical items the app note says are already in the tree are not rows here. They are listed in section 6.

Deny rules stay stricter than allow rules in every row. Skip-permissions stays off for unattended runs. None of these closes is a blanket bypass. External side effects stay on an ask unless a durable rule already allows that call.

| Gap | User impact | Evidence | How to close it | Effort |
| --- | --- | --- | --- | --- |
| Skip-permissions lifts forced asks, ask-rule asks, taint, outside-folder file cards, doom-loop cards, and the `run_python` bridge | One composer toggle runs mail, calendar, unsandboxed or out-of-root shell, and repeated identical calls without a card. Deny and hardline still hold. A tainted turn’s forced ask does not hold | App note: `lift_permission_ask` ignores `forced` and runs after taint, desk autonomy, and `resolve`. `STILL_ASK` is only `propose_plan` and `desk_ask`. `_bridge_approve` returns approved when skip is on. Market: `bypassPermissions` still prompts for ask rules, must-interact tools, and critical-path removals ([permission modes](https://code.claude.com/docs/en/permission-modes)) | In `permrules.lift_permission_ask` and the `app.py` skip block, leave the mode at `ask` when the card is `forced`, when an ask rule matched, when danger is `external` or `schedules`, or when `shell_run` was not already allowed by the read-only set or an allow rule. Point `_bridge_approve` at that same predicate. Keep `off`, refusals, `propose_plan`, and `desk_ask` as they are. Update the composer, Settings, and context-drawer copy so it no longer says cards are off | M |
| Unattended default is `ask`, and the Settings copy disagrees with the loop | A scheduled or job run is not on the fenced “deny unless already allowed” path leaders document for CI. Skip is already cleared, so the hole is the default of `unattendedApprovals`, not the skip flag. The app note never says the `ask` value waits, parks, or denies | App note: default `unattendedApprovals` is `ask`; `deny` records a denial and refuses; the loop clears skip for `job` and `scheduled`; Settings copy says jobs keep their own skip setting. Market: `dontAsk` auto-denies anything that is not allowed ([permission modes](https://code.claude.com/docs/en/permission-modes)); Codex `never` still leaves the sandbox in force ([developers](https://developers.openai.com/codex/agent-approvals-security)); a Desktop task left in Manual stalls ([scheduled tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks)) | Default `unattendedApprovals` to `deny` in `llm.py`. Keep the existing refuse branch. A durable allow rule or a tool mode that is already `on` still runs. A deny still refuses. Do not clear that by turning skip on. Change the Settings sentence so jobs and scheduled runs refuse leftover asks. Leave the `ask` override in place for a run a person is watching; do not define new wait behavior for it | S |
| Mail and calendar allows cannot name a recipient or calendar | The durable “always” for those tools is whole-tool `on` or a bare-name rule. The card cannot save “this recipient” or “this calendar”. `always_rule` already rejects a blanket `*` and requires a pattern, so today it cannot save a useful external rule | App note inference: Gmail and calendar have a bare subject. `subject_for` lists Bash, Read, Edit, and Agent only. `gmail_send` already compares To addresses in `_verify_sent`. Market: no fetched page defines a mail or calendar always-allow scope. Claude parameter rules are deny/ask on a top-level scalar and do not cover primary content fields; allow rules do not use that form ([permissions](https://code.claude.com/docs/en/permissions)) | Extend `subject_for` for the Google send and calendar write tools, using the addresses or calendar identity the verifier already reads. Teach `validate_saved_rules` and the card suggestions in `ApprovalRules.tsx` to require a pattern. Evaluate deny before allow, same as shell. Keep whole-tool `always_chat` / `always_global` from being the path the card offers for `external` tools; a whole-tool `on` can remain a Settings change and still loses to a deny | M |
| MCP slugs never enter `resolve` | A global deny cannot block an MCP tool. An `on` grant survives until the schema hash changes. Chat `off` is the only chat-scope mute | App note: the loop skips `resolve` for MCP; chat `off` removes the slug; `on` decays to `ask` only on hash mismatch. Market: Claude deny/ask/allow apply to MCP names, and `requiresUserInteraction` does not offer always-allow ([permissions](https://code.claude.com/docs/en/permissions), [permission modes](https://code.claude.com/docs/en/permission-modes)). Cursor has an MCP allowlist ([Run Modes](https://cursor.com/docs/agent/security/run-modes)) | Before the MCP grant is treated as `on`, run the global deny list against that slug in `permrules` or at the `app.py` MCP branch. A deny sets `refusal` and does not change the grant row. Leave schema-hash decay and quarantine in place. Do not let skip-permissions lift an MCP deny. Optional later: a tool flag, stored by Grain rather than trusted from the server, that never offers always-allow. Ignore server annotation hints as authority; the market notes say they are untrusted ([MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)) | M |
| Rules have no project or chat scope | A path or shell allow saved from one project applies to every project. Tool modes and MCP grants already have global / project / chat scope, so the rules editor is the odd one out | App note gaps: no per-project or per-chat rule lists. Market: Claude layers with deny beating every other layer, project allows waiting for trust ([permissions](https://code.claude.com/docs/en/permissions)); Codex project rules load only when trusted ([rules](https://developers.openai.com/codex/rules)); Cursor project file plus team priority ([Run Modes](https://cursor.com/docs/agent/security/run-modes)) | Add project and chat rule maps beside the existing mode maps. On evaluate, any scope’s deny or ask hits before any scope’s allow. A project allow must not punch through a global deny. Wire the lists through the Settings rules editor and the context drawer’s existing project and chat controls. A managed/team layer is out of scope for this single-user app | M |
| `Read` deny is a different subject from `Edit` | A saved “don’t read this path” rule does not, on the subjects the app note lists, refuse a write, copy, or trash of that path | App note: `subject_for` emits `Read` or `Edit`, not both. Inference, not a traced test. Market: a Claude `Read` deny also blocks Edit and Write on that path ([permissions](https://code.claude.com/docs/en/permissions)) | In `permrules` evaluation, a `Read` deny for a path also refuses the edit-tool subjects for that path. An `Edit` allow must not win. Confirm with an offline `resolve` test before changing match order | S |
| No second reviewer for boundary crossings | Leaders’ recommended hands-off modes are a classifier inside a sandbox, not a prompt skip. Grain’s hands-off control is skip-permissions | Market takeaway on safe unattended setups ([permission modes](https://code.claude.com/docs/en/permission-modes), [Run Modes](https://cursor.com/docs/agent/security/run-modes), [developers](https://developers.openai.com/codex/agent-approvals-security)). The MCP blog tells client authors to keep guarantees in deterministic controls ([annotations blog](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/)) | Do not build this as the unattended default. Ship the skip fence and the unattended deny default first. A later classifier, if added, runs only after deny, hardline, taint, and folder gates, and a deny or a forced ask never goes to it. Cursor’s own page says classifier instructions are not a guarantee | L |
| Shell network is a boolean, not a deny-wins host list | A shell with `shellNetwork` on has no host allow/deny in the notes. Domain rules leaders use for fetch and proxy are absent | App note, via README’s shell section: no network unless `shellNetwork`. Sandbox profile was not re-verified. Market: Codex and Cursor `deny` beat `allow`, and private addresses stay blocked ([developers](https://developers.openai.com/codex/agent-approvals-security), [sandbox.json](https://cursor.com/docs/reference/sandbox)) | Re-read the sandbox profile before changing it. Then extend the existing shell network gate with a host list where deny wins, including names that resolve to private addresses. Do not open network by widening skip | L |
| Protected-path set is narrower than the lists leaders publish, and the full local deny list was not re-audited | The file tools refuse `.env`, keys, credential directories, `/dev`, and `/proc`. The notes do not show `.git`, editor config, or shell rc files in that set. They also do not prove those paths are allowed | App note: `fsx.sensitive_reason` is the list that was read; `mac.allowed_path` was not re-audited. Market: Claude protected paths and Codex read-only `.git` / `.codex` roots ([permission modes](https://code.claude.com/docs/en/permission-modes), [developers](https://developers.openai.com/codex/agent-approvals-security)) | After reading `mac.allowed_path`, refuse or force-ask writes to `.git`, editor and app config directories, and shell rc files, using the same hard gate as `sensitive_reason`. An allow rule and skip must not clear a refusal | S, after the audit |
| Google verification misses the field the user often cares about, and stops at Google | A send can be `verified` while the body was not compared. A created sheet can be `verified` while seed cells were not. `calendar_ensure` is unchecked. Local, shell, browser, and MCP successes are trusted without a second read | App note: `_verify_sent`, `_verify_draft`, `_verify_doc`, `_verify_cells`, `calendar_ensure`, and the search that found `verify.attach` only in `google.py` | Extend the existing compare maps in `google.py` for send body, draft recipients, and sheet seed cells, and attach `verify` to `calendar_ensure`. Keep the “do not retry” error. A local byte compare is a separate, larger change in `fsx.post_write`; do not block the Google field fixes on it | S for the Google fields; M for `calendar_ensure` plus a local read-back |
| Session grants die on process restart | “Allow for this chat” disappears after a restart even though the approval row and the card can be reattached. The call on a dead chat still does not run | App note: `SessionGrants` is in-memory. SQLite `open_approval` and `_recover_runs` reattach cards. Market: Claude file-edit always-allow is also session-only; Bash always-allow is written to a repo-local settings file ([permissions](https://code.claude.com/docs/en/permissions)) | Leave session grants ephemeral. The durable path is the patterned allow rule in build 3. Do not serialize session grants into a blob the client can replay. The Agents SDK warns that a saved run state is capability-bearing ([human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)) | S to keep, not to build |
| No fire-count audit and no batch card | The rules editor cannot show which rule fired. Several asks in one round are separate cards | App note gaps: both absent. The market notes do not describe fire counts or a batch card | Not part of the top three. A counter incremented inside `resolve`, shown next to the existing rules editor, is the small version. Batching can wait | S for a counter |

## 5. The top three builds

In order. Each one is offline-testable. Each one keeps a deny ahead of an allow. None of them turns skip-permissions on for jobs or scheduled runs.

### 1. Fence skip-permissions

Skip may still lift an ordinary, unforced `ask` for a tool that is not an external side effect. It must stop lifting the cases that make it a blanket bypass.

Change `lift_permission_ask` and the skip block in `app.py`. Update `_bridge_approve` so `run_python` uses the same predicate. Update `SkipPermissionsToggle.tsx`, `Composer.tsx`, `SettingsModal.tsx`, and `ContextDrawer.tsx` so the copy matches the fence.

Leave in place: hardline and other refusals, mode `off`, plan-drafting blocks, propose-only `external` blocks, `propose_plan`, `desk_ask`, and the clearing of the flag for `job` and `scheduled`.

Acceptance, with no network and no real side effect:

- A deny rule, with skip on, still produces a refusal and the tool function is not called.
- An ask rule, with skip on, leaves the mode at `ask`.
- A tainted `external` tool, with skip on, stays `ask` and `forced`.
- A write that sets `fs_needs_ask`, with skip on, stays `ask`.
- The third identical executed call, with skip on, still produces a doom-loop ask.
- `propose_plan` and `desk_ask` stay `ask`.
- A `run_python` bridge approval follows the same predicate: a fenced case does not return approved.
- A synthetic unforced `ask` whose danger is `writes`, with no ask rule and no refusal, still becomes `on`. That is the residual skip behavior.
- A `job` or `scheduled` run with the conversation flag true still clears skip before the lift.

### 2. Make unattended runs deny leftover asks

Default `unattendedApprovals` to `deny`. Keep the branch that records a denied approval and refuses when the call would still ask. Do not route that case through skip-permissions. Fix the Settings sentence so it does not say jobs keep their own skip setting.

An allow rule that `resolve` already turned into `on`, or a tool mode that is already `on`, still runs. A deny still refuses. Desk `propose` turning `external` to `off` stays as it is.

Acceptance:

- A fresh settings object has `unattendedApprovals` of `deny`.
- A scheduled run whose mode is still `ask` after rules records a denial and does not call the tool, including when the conversation’s `skipPermissions` is true.
- The same scheduled run with a matching allow rule, and no deny, is `on` and is not refused by the unattended branch.
- The same scheduled run with both an allow rule and a deny rule does not call the tool.
- An interactive chat with skip off still waits on an ask. It does not pick up the unattended denial branch.

### 3. Patterned allow and deny for mail and calendar

`subject_for` gains a real subject for the Google send and calendar write tools, built from the recipient or calendar identity those tools already pass to verification. `validate_saved_rules` still requires a pattern and still rejects a blanket `*`. Card suggestions offer that patterned rule. For `external` tools, the card does not take `always_chat` or `always_global` as the standing grant. A whole-tool `on` remains possible from Settings and still loses to a deny rule and to the skip fence in build 1.

This is ahead of the fetched leader docs. Claude’s parameter form does not let an allow rule name a primary content field. The reason to build it is Grain’s own bare subject, plus the requirement that a durable exception for mail or calendar name the target.

Acceptance, through `resolve` and `POST /permissions/evaluate`, without calling Google:

- An allow rule whose pattern matches one recipient leaves a different recipient at `ask`.
- A deny rule for a recipient refuses that recipient even when an allow rule also matches, and even when the tool mode is `on` and skip is on.
- `always_rule` rejects a blanket `*` and rejects a rule that is not a subject of the pending call.
- A patterned allow saved from a non-forced card is what the next matching call sees as `on`.
- A forced card still does not write a standing rule.

The next build after these three, if one more is scheduled, is the MCP deny check in the table. It is the same deny-wins rule on the path that currently skips `resolve`.

## 6. Already in the working tree

Drop these. Older docs called them missing. The app note says the working tree has them. The skip-permissions toggle is included because the app note read it from the uncommitted files.

- File tools `fs_glob`, `fs_grep`, `fs_edit`, `fs_copy`, `fs_mkdir`, and `workspaceRoots`. The older “find / read / write / move / trash only” list is stale.
- Host `shell_run` under Seatbelt, with desk and root cwd, `workspaceRoots`, and network gated by `shellNetwork`. The older “shell only inside a container, desk cannot be mounted” claim is stale. The sandbox profile text itself was not re-verified in that pass.
- Argument rules in `permrules.py`, session grants, and `always_rule`. The older “permissions are only per-tool on / ask / off” claim is stale.
- Subagent registration and workflow registration from `Toolbox.__init__`.
- Deny, then ask, then allow inside `resolve`; suggested allow rules; `POST /permissions/evaluate`; forced approvals that allow rules do not downgrade. Later behavior the old spec does not describe: MCP skips `resolve`, skip-permissions exists, desks park.
- No 600 second auto-deny on the chat wait loop. Decisions include `always_session` and `always_rule`. Approvals are SQLite rows. Startup recovery can reattach a pending card. A live run is still woken by an in-memory future, and a dead chat does not execute the call.
- Sticky taint and `Toolbox.gate`. The README roadmap’s “live security defects” paragraph is behind `docs/research.md` and behind the code the app note read.
- `propose_plan` claimed once by argument digest.
- The skip-permissions control: composer button, Settings checkbox, context-drawer hint, `skip_permissions_on`, and `lift_permission_ask`. It is current behavior, and section 5 fences it. It is not a missing feature.

`docs/research/sota-cowork.md` “Where we are” and `docs/research/track-d-safety.md` §1.1 describe a system this tree replaced. README’s short feature bullet is incomplete. README’s later shell section matches the host shell. Use the app note, not those older pages, for what ships.
