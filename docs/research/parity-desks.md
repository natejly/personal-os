# Parity: Desks (parallel autonomous sessions)

Date: 2026-10-04. Branch: `worktree-harness-parity`. This compares Grain's cowork desks with current peer products. The Grain side covers `backend/personal_os/cowork.py`, `_launch_desk` / `_desk_supervisor` / `_run_desk` in `app.py`, `deskgate.py`, `workspace.py`, `subagents.py`, `workflows.py`, and the renderer's `CoworkView` / `DeskRail` / `DeskDetail` / `DeskReview`. Earlier write-ups are in `sota-cowork.md` and `sota-cowork-2.md`.

## Sources

- Claude Cowork: https://support.claude.com/articles/13345190, https://support.claude.com/en/articles/13947068-assign-tasks-to-claude-from-anywhere-in-cowork, https://claude.com/cowork
- Claude Code subagents / agent view: https://code.claude.com/docs/en/subagents, https://code.claude.com/docs/en/agent-view, https://claude.com/blog/how-and-when-to-use-subagents-in-claude-code
- ChatGPT agent (retired) / ChatGPT Work: https://help.openai.com/en/articles/11752874-chatgpt-agent. ChatGPT Work facts come from secondary sources only: https://noqta.tn/en/blog/chatgpt-work-openai-agent-layer-developer-guide-2026, https://manojgopanapalli.substack.com/p/deep-dive-chatgpt-work
- OpenAI Codex cloud: https://learn.chatgpt.com/docs/cloud, https://learn.chatgpt.com/docs/environments/cloud-environment, https://help.openai.com/nb-no/articles/20001545-using-codex-cloud, https://codex.danielvaughan.com/2026/04/01/codex-cloud-exec-best-of-n-attempts/
- Cursor Cloud Agents: https://cursor.com/help/ai-features/background-agents, https://www.morphllm.com/cursor-background-agents
- Devin: https://docs.devin.ai/, https://docs.devin.ai/work-with-devin/advanced-capabilities, https://docs.devin.ai/product-guides/session-insights.md, https://fast.io/resources/devin-ai-limits/
- Manus: https://help.manus.im/en/articles/11711218-how-can-i-take-over-manus-browser-or-vs-code, https://manus.im/docs/hi/features/browser-operator.md, https://www.manus.im/hi/blog/manus-wide-research-solve-context-problem, https://aiproductivity.ai/tools/manus/
- Google Jules: https://jules.google/docs/, https://jules.google/docs/usage-limits/, https://jules.google/docs/review-plan/, https://www.infoworld.com/article/4086269/agentic-coding-with-google-jules.html

## Inventory spot-check (what the code actually says)

| Claim | Verdict | Where |
|---|---|---|
| The deskMaxLive help text says desks "wait their turn", but nothing queues | **Confirmed.** `create_desk` returns 409 (app.py:7726). `_launch_desk` returns None when `_over_live_cap()` is true (app.py:3348-3360). `_wake_desk` returns None without a message after an approval, so the desk stays `blocked`. The help text is at CoworkSettings.tsx:181. | app.py, CoworkSettings.tsx |
| Wakes are lost at the cap | **Worse than the inventory says.** `_shell_wake` (app.py:3382) calls `toolbox.shell.drain_notes()` *before* `_launch_desk`. At the cap the launch returns None, so the background-job results are dropped permanently. The `_missed_wake` path in `_desk_supervisor` claims a run with `claim_run` and never checks the cap, so it can exceed deskMaxLive. | app.py:3285-3330, 3382-3390 |
| Gate state is kept in memory | Confirmed: it is the `tb.deskgate_state` dict. | deskgate.py:43 |
| A chained turn does not see tool results | **Stale.** `compaction.build_history` replays tool calls and results for the part of the history that has not been summarized (`_with_tools`, compaction.py:269-276), and `history_rows` keeps assistant rows that only ran tools. PROGRESS.md (NOTES_CAP 3000) adds memory on top of that; it is not the only memory. | compaction.py, repos.py:416 |
| Resume is offered only for blocked/paused/interrupted | Confirmed (DeskDetail.tsx:277), even though `RESUME_FROM` also includes awaiting_plan and review (app.py:3339). | DeskDetail.tsx |
| Accept all sends everything to `doc` | Confirmed (DeskDetail.tsx:255). `defaultDest` (DeskReview.tsx:24) also falls back to `doc` for every type. | DeskDetail.tsx, DeskReview.tsx |
| doc/doc_append promote the current bytes even when the output is stale | Confirmed. `_promote` reads the file with `_read_whole` and checks the sha only for `download` (app.py:8071-8110). A comment at app.py:7684 says "claim_output accepts a stale row anyway". | app.py |
| Nothing auto-resumes after a restart | Confirmed (`Desks.recover`, cowork.py:594). | cowork.py |
| There is no wall-clock ceiling on a desk | Confirmed. `JOB_HARD_SECONDS` wraps only scheduled runs (app.py:3176). | app.py |
| A desk cannot be given input files | Confirmed. `DeskIn` has only brief/title/project_id/autonomy/budget/start (app.py:7609). | app.py, CoworkView.tsx |
| Workspaces are never cleaned up | Confirmed. The only cleanup is `workspace.purge`, on a confirmed delete (workspace.py:549). | workspace.py |

## Feature comparison

Legend: Y = has it, P = partial, N = no, ? = not documented, n/a = not applicable.

| Capability | Grain | Claude Cowork | Claude Code bg/subagents | ChatGPT Work | Codex cloud | Cursor Cloud | Devin | Manus | Jules |
|---|---|---|---|---|---|---|---|---|---|
| Isolated workspace per session | Y (a folder per desk, paths checked after resolving symlinks) | Y (cloud env) | Y (git worktree) | Y | Y (container) | Y (VM) | Y (VM) | Y (VM) | Y (VM) |
| Keeps running after the client closes | N (the app must stay running; a restart interrupts the desk) | Y | P (supervisor daemon) | Y | Y | Y | Y | Y | Y |
| Resume after restart or wake | Manual Resume only | Y | Y (daemon restarts it) | Y | Y | Y | Y (sleep/wake) | Y | Y |
| Hand the session input files or folders | N (brief text only) | Y (folder grants) | Y (repo) | Y (plugins) | Y (repo) | Y (repo) | Y | Y | Y (repo) |
| Concurrency cap | Y (deskMaxLive 4) | n/a | Y (20 subagents) | ? | Y (attempts 1-4) | ? | none reported | 1-20 by plan | 3/15/60 |
| What happens over the cap | 409, or the desk sits `blocked` with no message | n/a | n/a | ? | n/a | ? | n/a | plan limit | "New task" disabled |
| Session list grouped by state | Y (Needs you / Working / Review / Done) | P | Y | P | Y | Y | Y | Y | Y |
| Peek or reply without attaching | Y (message box, question banner) | Y | Y (peek panel) | P | Y (follow-up) | Y | Y | Y | Y |
| Plan gate before acting | Y (each step bound by its args digest) | Y | Y (plan mode) | P | N | N | P | N | Y (auto-approves on a timer) |
| Unattended approvals | Y (parks after 180 s, wakes on decision) | P | Y (prompts surface in the main session) | ? | n/a | n/a | P | Y (asks the user to take over) | Y |
| Approval modes | plan / ask / propose | Manual / Auto / Skip | default / acceptEdits / auto / dontAsk / plan | confirms high-impact actions | n/a | n/a | auto-approve children toggle | n/a | approve plan |
| Completion check before "done" | Y (gate plus reviewer subagent) | ? | N | ? | N | P (test videos) | P (insights afterwards) | N | Y (Critic) |
| Output as a reviewable artifact | Y (deliver, then Accept with read-back) | Y (files) | Y (PR) | Y (files) | Y (diff/PR) | Y (PR + video) | Y (PR) | Y | Y (PR) |
| Per-turn undo | Y (run snapshots) | ? | via git | ? | via git | via git | via git | ? | via git |
| Browser takeover | Y (Take over window) | Y | P | Y (takeover) | N | Y (remote desktop) | Y | Y | N |
| Coordinator spawning children | Y (agent_spawn, depth 2, 4 concurrent) | Y | Y (depth 3, 20) | ? | N | N | Y (managed Devins) | Y (Wide Research, 100+) | N |
| Best-of-N attempts | N | N | N | N | Y (1-4) | N | N | N | N |
| Scheduled session | N (jobs only propose and cannot start a desk) | Y | N | Y | N | Y (Automations) | P | Y | N |
| Per-session budget | Y (turns and $, can only tighten) | quota | maxTurns | quota | quota | quota | ACU cap per session | credits | daily tasks |
| Wall-clock ceiling | N (per reply only) | none | none | ? | ? | ? | ACU | ? | ? |
| Idle reap / sleep | N | n/a | Y (~1 h unless pinned) | ? | 12 h container cache | ? | Y (~0.1 ACU idle) | ? | ? |
| Cleanup keeps unfinished work | P (delete asks whether to purge; nothing else is cleaned up) | ? | Y (keeps a dirty worktree) | ? | n/a | ? | ? | ? | ? |
| Notifications on needs-input, done and failed | Y (OS notification, only when unfocused) | Y | Y (hook) | ? | ? | ? | Y | ? | Y (browser) |
| Network egress control | Y (shell off/allowlist/open) | Y | Y | ? | Y (off by default) | Y | ? | ? | ? |

## Where Grain is ahead

- **Plan binding.** Each approved step authorizes exactly one call with byte-identical arguments (`plans.py` args_digest, single-use claim). Peers approve a prose plan, and after that the agent can do anything. Taint the plan did not predict voids the pre-approval.
- **Completion gate.** `desk_done` is refused while todo steps, plan steps or undelivered `outputs/` files remain, and a read-only reviewer also checks the work. Only Jules (Critic) has something comparable.
- **Verified promotion.** Every Accept is read back and compared before the row reads `promoted`. An agent can never promote on its own.
- **Parking.** If nobody is watching an approval, the turn ends without losing the card, and a later decision wakes the desk and tells it what was decided.
- **Per-turn undo/redo** of workspace changes, without needing git.

## Different by design (keep)

- Grain is local only. There is no cloud VM, so "keeps running after the laptop closes" is out of scope. The closest local equivalent is restart recovery (G4).
- Nothing auto-resumes by default after a crash, because a desk may have `started` calls whose outcome is unknown. G4 proposes an opt-in that skips those desks.
- External effects (mail, calendar, Tasks) always ask, and `propose` autonomy forces them off. Desks never auto-send.
- There is no pixel-level desktop control. The browser is driven over CDP, with a human taking over when needed.
- There is no best-of-N. For a single user on metered open models, N times the cost is rarely worth it.

## Gaps

| ID | Gap | Priority | Size |
|---|---|---|---|
| G1 | deskMaxLive refuses instead of queueing; approval and shell wakes are lost at the cap; the supervisor's retry bypasses the cap | P0 | M |
| G2 | Accept all sends binaries to `doc`; stale outputs are promoted without notice | P0 | S |
| G3 | The UI hides Resume and Pause for states the backend accepts | P1 | S |
| G4 | No opt-in resume of interrupted desks after a restart | P1 | M |
| G5 | No way to hand a desk input files or docs when creating it | P1 | M |
| G6 | Completion-gate state is lost on restart | P1 | S |
| G7 | Workspaces are never cleaned up, and `.trash` counts toward the quota | P2 | M |
| G8 | No desk-wide wall-clock ceiling | P2 | S |
| G9 | Scheduled jobs cannot start a desk | P2 | M |

### G1. A real queue for deskMaxLive (P0, M)
Peers: Jules disables "New task" at its concurrency limit and says so. Claude Code's agent view, Devin and Manus show waiting sessions explicitly. None of them silently strands a session.
Grain today: create/start/resume/message return 409. An approval decided at the cap leaves the desk `blocked` with no message (`_wake_desk`, app.py:3373). `_shell_wake` drains the job notes before a launch that fails, so the notes are lost (app.py:3382). `_missed_wake` claims a run without checking the cap (app.py:3285).
Proposal: add a `queued` status, plus columns `queued_at REAL` and `queued_message TEXT` (cowork.py STATUSES/SCHEMA, with a migration). `_launch_desk` returns a sentinel when it is over the cap. Each caller then calls `desks.enqueue(id, message, from_status)` instead of 409ing; the routes return 202 `{queued: true, position}`. `_shell_wake` drains its notes only after a successful launch, or stores them in `queued_message`. `_missed_wake` checks the cap. A `_drain_queue()` runs in the supervisor's `finally` and at startup: it launches the oldest queued desks while there is room. Stop and Delete work on a queued desk. The renderer gets a "Queued (n)" rail group with its position, and the help text becomes accurate.
Tests: test_desk_chain.py. With cap=1: a second start is queued and starts when the first settles; an approval decided at the cap queues and later runs; shell notes survive the cap; the `_missed_wake` path respects the cap; stopping a queued desk removes it from the queue.

### G2. Promotion correctness (P0, S)
Peers: a PR or diff is reviewed against the exact bytes that ship (Codex, Cursor, Jules).
Grain today: Accept all forces `doc` (DeskDetail.tsx:255), so xlsx/pdf/png end up in `promote_failed`. doc, doc_append and document promote the current bytes even when the row is stale (app.py:8071).
Proposal: `defaultDest(o)` chooses `doc` for text files, `document` for pdf/docx/xlsx/pptx, and `download` for anything else; Accept all uses it. Server side, `_promote` refuses any destination when the sha differs from the delivered one, unless `AcceptItem.accept_stale=true`. The Output tab shows "changed since delivery, accept the current file?".
Tests: test_cowork.py. A stale doc accept without the flag fails with "changed", and with the flag it succeeds. Accepting an xlsx with the default destination becomes `document`.

### G3. Action parity in DeskDetail (P1, S)
Peers: Claude Code's agent view offers the same keys in every state.
Grain today: DeskDetail.tsx:277 offers Resume only for blocked/paused/interrupted. Pause is limited to planning/working, while backend PAUSE_FROM/RESUME_FROM (app.py:3339-3345) accept more.
Proposal: export `RESUME_FROM`/`PAUSE_FROM`/`STOP_FROM` in GET /cowork/desks (`actions: string[]` per desk) and render buttons from that list, so the two sides cannot drift apart again.
Tests: backend: `actions` for each status matches the tuples. Renderer: a review desk shows Resume.

### G4. Opt-in resume after restart (P1, M)
Peers: Claude Code's supervisor restarts working sessions after a wake. Cloud sessions survive the client going away.
Grain today: `Desks.recover` (cowork.py:594) interrupts every live desk, and only a manual Resume restarts it.
Proposal: a setting `deskAutoResume` (default off). After `_cowork_startup`, every desk interrupted by `reason=restart` is resumed through G1's queue, but only if its last run has no `started`/unknown-outcome calls in `executed_calls` and no pending approvals. The others stay interrupted, with the reason "had an action whose outcome is unknown". This uses `_wake_desk` with DESK_RESUME. A checkbox goes in CoworkSettings.
Tests: test_desks.py / test_desk_chain.py. Off means nothing resumes. On, a clean desk resumes, a desk with an unknown call stays interrupted, and the cap is respected.

### G5. Input files for a desk (P1, M)
Peers: Cowork folder grants; the code agents clone a repo; Manus/Devin uploads.
Grain today: `DeskIn` (app.py:7609) carries only the brief, so the agent has to find files itself with ask-first local tools.
Proposal: `DeskIn.inputs: [{kind: "doc"|"document"|"path", id|path}]` plus POST /cowork/desks/{id}/inputs. Each input is copied once into `work/inputs/` as a snapshot. Docs are written as `.md`; local paths must be under home and are size-capped by the workspace quota. Copies are read-only and listed in `work/inputs/MANIFEST.md`, and `desk_manual` mentions them. Desk tools refuse writes under `work/inputs/`. In the renderer, NewDeskCard gets an "Add inputs" picker (docs search + Electron file dialog), and the Files tab shows them.
Tests: test_desk_workspace_tools.py. A doc input lands as .md, a path outside home is refused, the quota is enforced, and a write to inputs is refused.

### G6. Persist the completion-gate state (P1, S)
Grain today: refusals and whether the reviewer has run live in `tb.deskgate_state` (deskgate.py:43), so a restart grants two more refusals and another review.
Proposal: columns `gate_refusals INT`, `gate_reviewed INT` on `desks`. deskgate reads and writes them through `desks`, and they reset when a message starts new work.
Tests: test_deskgate.py. After a simulated restart (new Toolbox), the third call still passes and the reviewer does not run again.

### G7. Workspace lifecycle (P2, M)
Peers: Claude Code removes a worktree that has no changes and keeps a dirty one.
Grain today: nothing is cleaned up. `.trash` counts toward the 200 MB quota.
Proposal: a startup sweep removes `cowork/<id>` folders with no desk row. An "Empty trash" action clears `.trash`, and the quota excludes `.trash`. An opt-in `deskPurgeAfterDays` purges archived or done desks whose outputs are all decided. CoworkSettings shows total storage.
Tests: test_desk_workspace_tools.py / test_desks.py.

### G8. Desk wall-clock ceiling (P2, S)
Peers: Devin caps ACU per session; Claude Code has maxTurns.
Grain today: only scheduled runs get `JOB_HARD_SECONDS`.
Proposal: a `deskMaxMinutes` cap (budget key `maxMinutes`, tighten-only via `_desk_caps`). `_chain_kind` stops chaining once elapsed time passes the cap and the desk settles to `review` with reason "time". It is shown in the meter.
Tests: test_desk_chain.py.

### G9. Scheduled desks (P2, M)
Peers: Cowork saved tasks, ChatGPT Work, Cursor Automations and Manus can all start a session on a schedule.
Grain today: jobs can only propose, and `desk_start` is not available to them.
Proposal: a job kind `desk`, whose job holds a brief template, autonomy (plan or propose only) and caps. When it fires, it creates a desk through `create_desk(start=True)` and the queue (G1). Writes still go through the plan/approval gates, and outputs still need Accept. Jobs still never send mail.
Tests: a jobs test that a firing creates a desk in propose mode and respects the cap.

Not proposed: best-of-N; idle reaping (a desk between turns costs nothing locally); cloud continuation.
