## Agent loop, durable runs, approvals, plans and verified writes

### Where we are

**Loop.** `app._chat_stream` (backend/personal_os/app.py ~L1000-1600) is a hand-rolled ReAct loop. Each round it re-injects the todo plan last (`_reinject_plan`), streams `llm.stream_chat`, and executes the returned tool calls strictly sequentially. Guards:
- `Budget` (rounds / tokens / seconds / cost; `BUDGET_STOP`, `SOFT_NUDGE`). `maxToolRounds` is 25 by default, and stays that way.
- A loop breaker that only fires on the same `call_key` (name + args) five times consecutively (`REPEAT_LIMIT`).
- A per-tool breaker after 3 consecutive errors (`TOOL_ERROR_LIMIT`).
- Tool results go through `tool_results.for_model` as paged handles.
- No detection of alternating A/B calls, same call returning the same result, or error cycles across different args.
- No retry policy for transient failures in `Toolbox.call` (tools.py ~L505). It converts exceptions to `tool_error` and the model has to retry.
- I found no mid-run context compaction in app.py.

**Durable runs.** runs.py `RunStore` over db.py tables:
- `agent_runs` (status running | awaiting_approval | done | error | interrupted, budget JSON, last_seq, desk_id/turn).
- `run_events` (run_id, seq, type, data, ts). This is the write-through tape; the in-memory ring is a hot cache, and SSE is `?since=` tail.
- `approvals` (call_id, args_digest, forced, danger, status pending | approved | denied, decided_by incl. 'park').
- `executed_calls` (key = sha256(run_id, step, tool, args_digest); status started | done | error; `call_once` replays `done`, returns `unknown_outcome` for `started`, and re-runs `error`).
- `RunStore.recover()` at startup marks orphaned runs `interrupted`. `_recover_runs` salvages the partial reply and tool events from the tape into the message.
- **Weak spot:** a chat run that dies stays dead. `POST /approvals/{call_id}` for a dead run records the decision, patches the tool event to "Not run: the reply was interrupted... ask again", and does nothing else. Only desks can resume (`_wake_desk`).
- The in-reply tool transcript (`messages` with role=tool) exists only in memory. `convos.history()` replays message content only, so after a crash nothing the model could use to continue survives except what the tape holds.

**Approvals.** Per-tool mode on | ask | off, resolved chat > project > global > tool default (`Toolbox.effective`), with danger tiers (`DEFAULT_MODE`: external, plan and schedules default to ask). `gate()` forces ask for external tools once the run is tainted. Approvals are rows that wait forever; desks can be parked after `parkAfterSeconds`. "Always" (always_chat / always_global) is a whole-tool grant. It is blocked when `forced` and for `propose_plan`. MCP grants are bound to `schema_hash`. There are no argument-scoped rules, so "always allow write_local_file" means any path under home. There is no deny-by-pattern.

**Plans.** plans.py `propose_plan` is an approval artifact (`action_plans`, `plan_steps`). Each step is bound to `args_digest`, single-use via an atomic `claim`, and run-scoped. Forced approvals never consult a plan. The user can edit steps, which re-derives the digest. `taint_expected` stops plan research from re-gating later external steps. Plan mode withholds non-`PLAN_SAFE_DANGER` tools. Desk autonomy modes are plan, ask and propose.

**Verified writes.** verify.py `check()` reads back and diffs after an external write; `Toolbox.call` ends in `checked(name, out)`, so UNVERIFIED is an error. outbox.py holds Gmail sends for 90s with undo. Job runs (`proposal_only`) turn external calls into proposals in two places (`_call_tool` and `Toolbox.call`). **Weak spot:** local file writes have no pre-image. `mac.write_local` mode='overwrite' or 'append' destroys or alters the previous content with no undo, and `move_local_file` has no inverse. Only trash is recoverable (Finder). Docs writes have revision history. Local files are the one verified-write surface with no rollback.

**UI.** PlanApproval.tsx / ActionPlanCard.tsx (one card, edit/accept/reject), ToolEvents.tsx (pending approval cards, forced/taint badges), ToolPermissions.tsx (per-tool mode matrix), AgentInbox.tsx (interrupted chip, no action).

### What the best open-source systems do

- **LangGraph** ([persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)).
  - A checkpointer persists graph state per `thread_id` at every super-step. Backends include SqliteSaver and PostgresSaver, and docs warn about unbounded checkpoint growth.
  - `get_state_history` plus `update_state` give time travel and forking from any past checkpoint.
  - `interrupt()` raises an exception and persists. `Command(resume=value)` re-executes the node from its start, so anything before the interrupt must be idempotent. Do not wrap it in try/except. Interrupt order must be deterministic, because resume values are matched by index. Parallel interrupts resume via an id-to-value map.
  - Our `executed_calls` is the stronger half of this. Our gap is that nothing re-enters a dead chat run.
- **OpenAI Agents SDK** ([HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/), [guardrails](https://openai.github.io/openai-agents-python/guardrails/)).
  - `needs_approval` is a bool or an async predicate over (context, parsed args, call id). Malformed args default to requiring approval.
  - A pause surfaces `result.interruptions` as `ToolApprovalItem`s. `result.to_state()` yields a JSON-serializable `RunState`, which can resume in another process via `state.approve()` / `state.reject(rejection_message=...)`.
  - `always_approve` / `always_reject` are sticky for the rest of the run and survive serialization.
  - Guardrails are input (parallel or blocking), output, and per-tool-call (before and after execution, tripwire exception). The tool guardrails cover local MCP tools.
- **OpenHands** ([StuckDetector](https://docs.openhands.dev/sdk/guides/agent-stuck-detector)). On by default. Five patterns, compared semantically (tool + content, ignoring timestamps):
  - same action and same observation 4+ times;
  - same action erroring 3+ times;
  - 3+ consecutive agent messages with no user input;
  - A/B alternation for 6+ cycles;
  - repeated context-window errors.
  - Detection yields a stuck state the conversation handles by stopping or correcting.
- **Goose** ([permission modes](https://deepwiki.com/block/goose/6.2-permission-modes-and-tool-approval), [tool-repetition limits](https://github.com/aaif-goose/goose/issues/12150)).
  - A stacked inspector pipeline per tool call (Security, Egress, Adversary, Permission, Repetition). Each can allow, deny or require approval.
  - Modes are auto, approve, smart_approve (read-only auto, others asked) and chat.
  - Per-tool persisted AlwaysAllow / NeverAllow / AskBefore.
  - The AdversaryInspector makes a second LLM call to review calls against user-written rules.
  - A configurable max-repeat limit lives in the RepetitionInspector.
- **Claude Code permissions** ([docs](https://code.claude.com/docs/en/permissions)).
  - Rules look like `Tool(specifier)`, with `*` wildcards (`Bash(git log *)`, `WebFetch(domain:x.com)`, `Tool(param:value)` for deny/ask).
  - Evaluation order is deny, then ask, then allow, first match wins, and specificity never overrides. An allow cannot carve an exception out of a deny.
  - A bare-tool-name deny removes the tool from the model's context.
  - Scopes are managed > local > project > user.
  - Compound commands are split and each part matched. Deny and ask match any sub-command, and wrappers are stripped.
  - Rules are explicitly "not a security boundary" for bypass forms, so a sandbox is paired with them.
  - Checkpoint/rewind of file edits is a separate feature.
- **Cline / Roo** ([Cline checkpoints](https://docs.cline.bot/core-workflows/checkpoints)).
  - Cline snapshots the workspace into a shadow git repo after every tool use, with a Compare (diff) button and three restore modes: files only, task only, or both.
  - Large repos are a known cost.
  - Roo's modes (Architect, Code, Ask, Debug) restrict tool groups and file patterns per mode. I could not re-fetch the Roo page (redirect), so that part is from memory.
  - Our desks' plan/ask/propose autonomy is the equivalent.
- **smolagents** ([ReAct](https://huggingface.co/docs/smolagents/conceptual_guides/react)). A `MultiStepAgent` with memory steps (`TaskStep`, `ActionStep`, `PlanningStep`), step callbacks, and periodic re-planning via `planning_interval`. `final_answer_checks` validate the answer before it is accepted. Our todo re-injection covers the planning half.
- **Temporal-style durable execution** ([activity definition](https://docs.temporal.io/activity-definition)). A workflow is a deterministic function of its event history. Side effects live in activities with retry policy (backoff, max attempts, non-retryable error classes) and timeouts. Activities can run more than once, so idempotency keys are mandatory. Our `executed_calls` is this idea. We lack retry policy and replay-after-crash.

### Gaps

| # | gap | who does it | impact | effort |
|---|---|---|---|---|
| 1 | A dead chat run cannot be resumed; the decided approval and the work done so far are discarded | LangGraph resume, Agents SDK RunState, Temporal replay | High: a crash or restart mid-task today means starting over, and "ask again" is the only recourse | M |
| 2 | Local file overwrite/append/move has no pre-image and no undo | Cline checkpoints, Claude Code rewind | High: the only unrecoverable ask-first write we ship | M |
| 3 | Loop detection is only 5 identical consecutive calls | OpenHands 5 patterns, Goose RepetitionInspector | Medium: alternating and same-result loops burn budget until the cap | S |
| 4 | No argument-scoped permission rules (allow/ask/deny by path, domain, recipient) | Claude Code, Goose per-tool, Agents SDK predicate `needs_approval` | High: "Always" is all-or-nothing. A deny-only variant is safe under our anti-goals, but allow rules for external tools conflict with ask-first | M |
| 5 | Time travel / fork from an earlier message (regenerate only drops the last assistant message) | LangGraph, Cline restore task | Medium | L |
| 6 | No tool-level guardrail or second-opinion review before external writes | Agents SDK tool guardrails, Goose AdversaryInspector | Medium; needs an extra model call, and privacy review for what is sent | M |
| 7 | Read-only tool calls in one round run sequentially | Agents SDK, Goose parallel batches | Medium (latency); interacts with the approval ordering | M |
| 8 | No transient-failure retry policy for idempotent reads | Temporal activities | Low-medium | S |
| 9 | No mid-run context compaction; relies on paged handles only | OpenHands condensers | Medium, long tasks only | L |

### Build next

**loop-1 (resume an interrupted chat run).** A "Resume" action on an interrupted reply starts a new run linked by `resumed_from`. The model gets a system note, built from the tape, saying what the dead run already did: the partial text, completed calls with previews, and calls that started with unknown outcome (told not to repeat them). Taint is re-derived from the tape's `taint` events, so resuming cannot launder untrusted content. `call_once` gains an `inherit` run id, so an identical external call that already completed returns its recorded result, and one that started returns `unknown_outcome`, rather than executing twice. This is LangGraph's resume plus Temporal's idempotency on top of what `executed_calls` already gives us. It is user-initiated, so it is not a heartbeat. No auto-resume.

**loop-2 (file pre-image snapshots and undo).** Before `write_local_file` overwrite/append and `move_local_file`, copy the old bytes to `data_dir/snapshots/` and record a row. The tool result carries `undo: {snapshot_id}`. A restore route refuses if the file changed since the agent's write (digest mismatch) unless forced. A restore is itself snapshotted, so undo is undoable. The UI gets an Undo button on the tool event. Retention is capped by age and bytes. This is Cline's checkpoint model scoped to the files the agent actually touches.

**loop-3 (stuck detector).** A pure `stuck.py` detector observes (tool, args digest, result digest, error) per call and recognises: same call and same result 4 times; same call erroring 3 times (any consecutive position); A/B alternation for 3 cycles; the same tool erroring with different args 4 times. The first hit appends a corrective note to the tool result. The second ends tool use through the existing `partial="loop"` path and `_final_round`. Gated by a new `stuckDetection` setting. It supplements `REPEAT_LIMIT`, does not raise `maxToolRounds`, and adds no new gate bypass.

Not chosen, with reasons: arg-scoped rules (#4) need a settings UI and a policy decision on allow rules for external tools, so it is a follow-up spec once deny/ask-only semantics are agreed. Fork/time travel (#5) is an L effort.

### Specs

#### loop-1: Resume an interrupted chat run (tape-derived resume note + cross-run idempotency) (M)

**Why.** A chat run that dies (backend restart, crash) stays dead: POST /approvals for it only patches the card to 'ask again', and the in-reply tool transcript existed only in memory. LangGraph (checkpoint + Command(resume)), OpenAI Agents SDK (serialized RunState) and Temporal (event-history replay, idempotent activities) all let an interrupted agent continue. We already persist the tape (run_events) and the idempotency journal (executed_calls), so the missing piece is a user-initiated resume that cannot double-execute external writes or launder taint.

**Files.** `backend/personal_os/resume.py (new)`, `backend/personal_os/runs.py (RunStore.prior_call, call_once inherit param)`, `backend/personal_os/db.py (agent_runs.resumed_from migration)`, `backend/personal_os/app.py (ChatIn.resume_of, small branch in _chat_stream, _call_tool passes inherit, POST /runs/{run_id}/resume)`, `backend/tests/test_resume.py (new)`, `src/renderer/src/components/Message.tsx (Resume button on interrupted message)`, `src/renderer/src/api.ts or equivalent (resumeRun)`

**Design.** DB: add to db.py _migrate `wanted['agent_runs']['resumed_from'] = 'TEXT'` (nullable run_id of the run this continues). No new table.

resume.py (pure, no app imports):
- `RESUME_NOTE` template constant.
- `resumable(run: dict, latest_for_conv: dict|None, answering: bool) -> tuple[bool, str]`: ok only if run['status']=='interrupted', run['kind']=='chat', run['desk_id'] is None, not answering, run is latest run of its conversation, and no other run has resumed_from == run_id (pass a bool `already_resumed`). Returns (False, reason) otherwise.
- `taint_from_tape(events: list[RunEvent]) -> list[str]`: sources from every `taint` event (`data['source']`). Resumed run must start tainted if non-empty.
- `build_resume_note(run: dict, events, executed: list[dict], approvals: list[dict], max_text=1500, max_preview=300) -> str`: one system-message string: the partial reply text (reuse RunStore.transcript logic: delta events for run['message_id']), a bullet list of finished tool_result events (name, args shortened with the same 300-char rule as app._short, error flag, result_preview truncated), a list of executed_calls with status 'started' phrased 'started, outcome unknown: do NOT repeat it; tell the user to check', a list of approvals still pending or decided-but-unused phrased 'was waiting on your approval; it was NOT run; ask again if still needed'. End with: 'Continue the task from here. Do not redo completed steps.' Deterministic ordering, no timestamps.

runs.py:
- `RunStore.prior_call(run_id, tool, digest) -> dict|None`: SELECT from executed_calls WHERE run_id=? AND tool=? AND args_digest=? ORDER BY created_at DESC LIMIT 1 (ignore step).
- `call_once(..., inherit: str|None=None)`: before inserting a new key, if inherit and the own key does not exist, look up prior_call(inherit, tool, digest): status 'done' -> return (json result, True) and ALSO insert a 'done' row under the new key so later replays are local; status 'started' -> return (unknown_outcome(tool, prior key), True); 'error' -> fall through and run normally.

app.py (keep edits localized):
- `ChatIn.resume_of: str|None = None`.
- In `_chat_stream`, in the branch that handles `body.content is None` (regenerate), add an earlier branch `if body.resume_of:` that: loads the interrupted run row, events=run_store.events(id), executed=run_store.executed(id), approvals=run_store.approvals(None, run_id=id); DOES NOT delete the salvaged assistant message (it stays visible as history); sets user_text to the last user message; after `messages = [system] + history` appends `{role:'system', content: resume.build_resume_note(...)}`; initialises `tool_ctx['tainted']` / `taint_sources` additionally from `taint_from_tape` (never clears existing taint). Resume runs must not skip any gate: they go through the same mode/gate/approval code path as any reply.
- `_call_tool`: pass `inherit=(run.input or {}).get('resume_of')` into `run.store.call_once`.
- Route `POST /runs/{run_id}/resume`: 404 if no run, 409 with `{reason}` if `resume.resumable` is false, else `bus.start(conv_id, lambda r: _run_chat(r, ChatIn(resume_of=run_id)), input={'resume_of': run_id})`, then `run_store._exec('UPDATE agent_runs SET resumed_from=? WHERE run_id=?')` via a new tiny `RunStore.set_resumed_from` method (note `update()` has an allow-list: add 'resumed_from' to it). Return {run_id, seq}.
- Include `resumed_from` and `resumable: bool` in GET /runs/{run_id} output.
- Never auto-called anywhere: no startup auto-resume, no timer (anti-goal: no heartbeat).

UI: in Message.tsx where an assistant message shows an error that begins 'Interrupted:' and the latest run for the conversation is interrupted (GET /runs?conversation_id=ID&status=interrupted&limit=1), render a 'Resume' button that POSTs /runs/{run_id}/resume and attaches to the new run's SSE like a normal send. Hide it once resumed (resumable false).

**Tests.** backend/tests/test_resume.py in the style of test_runs.py/test_propose_plan.py (TestClient, llm.stream_chat monkeypatched with a scripted async generator, tempdir PERSONAL_OS_DATA_DIR, no network). Cases: (1) Build a dead run by hand: convos.create + add_message(user) + assistant message, run_store.create, append run_events (delta, tool_result, taint), insert executed_calls via call_once with a stub fn, run_store.update(status='interrupted'). POST /runs/{id}/resume -> 200; the scripted llm receives a system message containing the partial text and 'Do not redo completed steps'. (2) A tool call in the resumed run identical (tool+args) to a 'done' journal row of the old run returns replayed=True and the tool fn counter stays at 1. (3) A 'started' row yields unknown_outcome and the fn is never called. (4) Taint from the old tape: the resumed run's first external call (e.g. gmail_send stub with mode 'on') is forced to ask (needs_approval true, forced true). (5) 409 when run status is 'done', when the conversation is answering, when already resumed (second POST), and for desk runs. (6) Unit tests for build_resume_note truncation and deterministic output, and RunStore.prior_call. (7) The old pending approval stays pending and is mentioned in the note, never auto-approved.

**Done when.** Killing the backend mid-reply, restarting, and clicking Resume continues the task in a new run with a note of what already happened; no external write executes twice (done calls replay, started calls return unknown_outcome); a tainted conversation stays tainted; all existing tests (test_runs, test_durable_runs, test_approvals, test_propose_plan) still pass; offline test suite test_resume.py passes.

#### loop-2: Pre-image snapshots and one-click undo for local file writes (M)

**Why.** write_local_file overwrite/append and move_local_file are ask-first but irreversible: mac.write_local truncates the file, with no backup. Cline snapshots the workspace after every tool use with Compare/Restore, and Claude Code offers rewind of file edits; Gmail sends here already have a 90s undo and docs have revision history, so local files are the only verified-write surface with no rollback.

**Files.** `backend/personal_os/filesnap.py (new)`, `backend/personal_os/db.py (file_snapshots table in SCHEMA)`, `backend/personal_os/tools.py (wrap write_local_file and move_local_file: capture before, attach undo to result)`, `backend/personal_os/app.py (small router: GET /file-snapshots, POST /file-snapshots/{id}/restore, startup prune)`, `backend/personal_os/llm.py (DEFAULT_SETTINGS keys)`, `backend/tests/test_filesnap.py (new)`, `src/renderer/src/components/ToolEvents.tsx (Undo button + restored state)`

**Design.** DB (SCHEMA, CREATE TABLE IF NOT EXISTS file_snapshots): snapshot_id TEXT PK (new_id), conversation_id TEXT, message_id TEXT, call_id TEXT, op TEXT ('overwrite'|'append'|'move'|'restore'), path TEXT (the path acted on), from_path TEXT (move source, else NULL), before_path TEXT (blob under data_dir/snapshots/<snapshot_id>, NULL if file did not exist or was too large), before_digest TEXT, before_existed INTEGER, after_digest TEXT, status TEXT DEFAULT 'live' ('live'|'restored'|'expired'), created_at REAL, restored_at REAL. Index on (conversation_id, created_at).

Settings (must be added to llm.DEFAULT_SETTINGS or PUT /settings drops them): `fileSnapshots: True`, `fileSnapshotMaxBytes: 5_000_000` (per file), `fileSnapshotRetainDays: 14`, `fileSnapshotBudgetMB: 200`. Add the two numeric ones to NUMERIC_SETTING_RANGES in app.py.

filesnap.py (class FileSnapshots(db, root: Path, settings_fn)):
- `capture(op, path, ctx, to=None) -> dict|None`: resolve via mac.allowed_path semantics (reuse mac._writable_path/allowed_path; never operate outside home). If op in overwrite/append and file exists and size<=max: copy bytes to blob (shutil.copyfile, mode 0600), compute sha256, insert row, return {snapshot_id}. If the file did not exist (create): row with before_existed=0 so undo means 'trash the created file' (use mac.trash_local, never delete outright). For move: record from_path and destination, no blob. If too large: return {'snapshot_id': None, 'reason': 'file larger than limit, no undo'}; never block the write.
- `finalize(snapshot_id, path)`: after a successful write, store after_digest = sha256 of the file as written.
- `restore(snapshot_id, force=False) -> dict`: only status 'live'. Check current file digest == after_digest; if not and not force raise Conflict ('file changed since the agent wrote it'). Before restoring, capture a new snapshot with op='restore' (so undo is undoable). Overwrite: copy blob back atomically (write temp + os.replace). Created file: mac.trash_local. Move: move back via mac.move_local only if the source path does not exist. Mark row 'restored'.
- `prune()`: delete blobs and mark expired for rows older than fileSnapshotRetainDays, then oldest-first until total blob bytes <= budget. Called from the existing startup hook (add one line to an existing on_event('startup') function, do not add a new background task).
- `list(conversation_id=None, limit=50)`.

tools.py: in write_local_file, when mode in overwrite/append or path does not yet exist, call `snap = await asyncio.to_thread(self.filesnap.capture, ...)` before `mac.write_local`; on success call finalize and merge `{'undo': {'snapshot_id': id}}` into the result dict; on LocalPathError/OSError nothing is kept (delete the just-made row). Same in move_local_file. Toolbox gets a `filesnap` attribute set the same way `outbox` is passed (constructor kwarg default None; if None everything is skipped, so existing tests are unaffected). The risk to preserve: verify.checked() must still see the same result shape; undo key is an extra key only.

Privacy: blobs stay under the data dir, are never sent to the model (result carries ids only), and are excluded from any export/sync path. Undo is a user action (route), never a model tool, so the model cannot restore or delete snapshots.

Routes (app.py, ~30 lines, or a small APIRouter in filesnap.py included once): GET /file-snapshots?conversation_id=, POST /file-snapshots/{id}/restore body {force: bool} -> 200 {ok, path}, 404 unknown, 409 on Conflict or already restored.

UI: ToolEvents.tsx - for a tool_result of write_local_file/move_local_file whose result carries undo.snapshot_id, show an 'Undo' button; on 409 conflict show a confirm 'File changed since; restore anyway?' that retries with force true; after restore show 'Restored'. The event preview must carry the snapshot id: extend the tool event payload by reading it from the result dict in app.py where `event` is built (add 'undo' key from result.get('undo')).

**Tests.** backend/tests/test_filesnap.py, plain script style with a tempdir used as HOME (monkeypatch os.environ['HOME'] / mac.home) and PERSONAL_OS_DATA_DIR; no network, no model. Cases: overwrite then restore returns the original bytes; append then restore; create then restore trashes the new file (monkeypatch mac.trash_local to move into a temp trash dir); move then restore moves back, and refuses when the source path is occupied; Conflict when the file was edited after the agent's write, force=True succeeds; restore creates its own 'restore' snapshot, restoring that gets the agent's version back; file over the byte limit gives snapshot_id None and the write still succeeds; prune by age and by byte budget removes blobs and flips status; path outside home or under ~/Library is rejected by capture (same errors as mac); a failing write leaves no dangling live row; route tests via TestClient for 404/409; Toolbox with filesnap=None behaves exactly as before (run existing test_mac_tools.py unchanged).

**Done when.** After the agent overwrites ~/Desktop/x.md, the tool event shows Undo, clicking it restores the previous bytes, and a second undo-of-the-undo works; moves can be reversed; conflicts are never silently clobbered; snapshots are pruned by age and size; the model has no tool that can restore or delete snapshots; test_mac_tools.py and test_verify_writes.py still pass; test_filesnap.py passes offline.

#### loop-3: Stuck detector: alternating, same-result and error-cycle loop patterns (S)

**Why.** Our only loop breakers are 5 identical consecutive calls (REPEAT_LIMIT) and 3 consecutive errors for one tool. OpenHands' StuckDetector detects five patterns (same action+observation 4x, action-error 3x, monologue, A/B alternation 6 steps, context-window errors) and Goose has a configurable RepetitionInspector. A/B ping-pong and same-result loops with slightly varied args run to the round or budget cap today.

**Files.** `backend/personal_os/stuck.py (new)`, `backend/personal_os/app.py (instantiate once per reply, observe after each tool result, check before each call: ~15 lines near the REPEAT_LIMIT block and after tool_results.for_model)`, `backend/personal_os/llm.py (DEFAULT_SETTINGS stuckDetection)`, `backend/tests/test_stuck.py (new)`

**Design.** stuck.py, pure:
- `@dataclass Obs: tool:str; args_digest:str; result_digest:str; error:bool`.
- `class StuckDetector(limits=Limits())` with `observe(tool, args, result) -> None` (digests via runs.args_digest for args; result digest = sha256 of canonical JSON of the result with volatile keys removed: duration_ms, ts, timestamps, idempotency_key, replayed) and `check() -> Stuck|None` where `Stuck(pattern:str, tool:str, detail:str)`. Keep a bounded deque (last 24).
- Patterns, evaluated in this order on the tail of the deque: (a) 'same_result': last 4 observations have identical (tool, args_digest, result_digest); (b) 'error_cycle': last 3 observations are the same (tool, args_digest) and all error; (c) 'alternating': last 6 observations are A,B,A,B,A,B with A != B (compare tool+args_digest) and no new results (each of A's result digests identical, each of B's identical); (d) 'error_storm': last 4 observations are all errors from the same tool with different args. Do not flag when the results differ (paging with offset legitimately repeats a tool).
- Constants in the module: SAME_RESULT=4, ERROR_CYCLE=3, ALTERNATIONS=6, ERROR_STORM=4. Message templates STUCK_NUDGE ('You appear to be stuck: {detail}. Change approach: use a different tool or arguments, or answer with what you have and say what is missing.') and the final stop reuses app.LOOP_STOP-style text.

app.py wiring: `detector = StuckDetector() if cfg.get('stuckDetection', True) else None` next to `last_sig`. After each executed call (where `tool_errors` is updated), `detector.observe(c['name'], args, result)` for calls that were actually executed (not denied/blocked/plan-claimed-pre results, not `pre` results). Then `s = detector.check()`: first hit for a given (pattern, tool) -> append `STUCK_NUDGE` as a string suffix into the content already built for the tool message (for_model result + a 'stuck_notice' key if it is a dict) and emit a `tool_result` event with `breaker='stuck_nudge'`; second hit (any pattern) -> `partial='loop'` which routes through the existing LOOP_STOP and `_final_round` path. It must not touch REPEAT_LIMIT, maxToolRounds, budget, approvals or plan logic. Add `'stuck'` into the trace span payload via tracer.end result field.
Setting: `stuckDetection: True` in llm.DEFAULT_SETTINGS (boolean; not numeric, no range entry needed). Optionally surface a toggle in Settings later; not required.

**Tests.** backend/tests/test_stuck.py, plain script, no app import for the unit part. Cases: same call+same result 4x -> 'same_result'; same call with different results (paging offsets) never flags; 3 identical erroring calls -> 'error_cycle'; A,B,A,B,A,B with stable results -> 'alternating'; the same sequence with a changed result in the middle -> None; different args erroring 4x on one tool -> 'error_storm'; volatile keys (duration_ms) do not defeat digesting; deque bounded at 24. Integration part (TestClient + monkeypatched llm.stream_chat scripted to emit alternating calls to two read-only tools such as list_documents/search_memory with stub results): the first detection puts a stuck notice in the tool message the model sees (assert on messages passed to the scripted llm), the next detection ends tool use with partial='loop' and a final tool-free round happens; with `stuckDetection=false` set via PUT /settings the run proceeds to the old REPEAT_LIMIT behaviour. Existing tests (test_working_memory, test_runs, test_approvals) pass unchanged.

**Done when.** A scripted A/B ping-pong or same-result loop is nudged on first detection and stopped on the second, well before maxToolRounds (which is unchanged at 25); legitimate paging and repeated reads with changing results are never flagged; the setting turns it off; offline tests pass.

