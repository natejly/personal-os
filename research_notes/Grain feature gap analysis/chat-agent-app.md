# Chat and the agent loop

Working tree as read on 2026-10-02, including uncommitted edits in `backend/personal_os/app.py`, `llm.py`, `context.py`, `subagents.py`, `src/renderer/src/components/Composer.tsx`, `ContextDrawer.tsx`, `src/shared/types.ts`, and the new `SkipPermissionsToggle.tsx`. Behavior below is what those files do. This note did not run the app or the test suite, and it did not split each claim into committed vs dirty.

## How does a chat turn work end to end?

### Takeaway

A chat turn is a background run. The composer posts to `POST /conversations/{id}/chat`, `chat()` starts `_run_chat` → `_chat_stream`, and every window tails that run’s stored event tape with `GET /conversations/{id}/stream`. Projects are containers: a conversation stores `project_id`, and the project’s name, description, and `system_prompt` are part of the stable system prefix for that turn.

### Cited Findings

**Shipped — composer, create, send, steer**

- `Composer` clears the draft, calls `send`, and restores the text if `send` returns false. Enter sends; Shift+Enter is a newline. While a reply is answering, the placeholder is “Steer the reply…” and Send stays available beside Stop. — [Composer.tsx](src/renderer/src/components/Composer.tsx)
- `send` steers when `streaming.answering` is set (`POST /conversations/{id}/steer`). Otherwise it calls `runStream`. A draft with no conversation id creates one with `draftProjectId` and `draftModel ?? settings.defaultModel`, then patches effort, fast mode, and upload-taint onto that row before the first run. A second send while that create is in flight waits and goes into the same chat. — [store.ts `send`](src/renderer/src/store.ts)
- `POST /conversations/{id}/steer` persists the user message, publishes `user_message`, appends it to `run.steers`, and `poke()`s the run. It 409s when nothing is answering. — [app.py `steer_run`](backend/personal_os/app.py)
- Inside the loop, a steer cancels the provider read. If the current segment already streamed or ran tools, it `finish_message`s that assistant row, yields `done` with `segment: true`, opens a new assistant message, and continues. Steered user text is appended after the tool results. — [app.py `_chat_stream`](backend/personal_os/app.py)
- `POST /conversations/{id}/chat` 409s when `bus.answering(id)` is set, and returns `{run_id, seq}`. The comment states that a run still in its auto-learn tail is not “answering,” so a new message is a new run. — [app.py `chat`](backend/personal_os/app.py)
- A 409 from `api.chat` makes the window attach to the other run and steer the text into it. — [store.ts `runStream`](src/renderer/src/store.ts)

**Shipped — streaming**

- `watchRun` subscribes with `chatStream(convId, seq, abort, runId)`. One subscription per conversation; a second viewer of the same `run_id` is ignored. Abort detaches that window. The comment on `Streaming` says abort does not end the run; `api.stopRun` does. — [store.ts `watchRun`](src/renderer/src/store.ts)
- `GET /conversations/{id}/stream?since=&run_id=` tails `run_events`. `run_id` pins the run so a reconnect cannot land on a newer run with a stale seq. Detaching a client does not touch the run. A run no longer in memory replays from the table and ends. — [app.py `stream_conversation`](backend/personal_os/app.py)
- `_chat_stream` yields, among others, `user_message`, `title`, `assistant_message`, `span`, `delta`, `reasoning`, `tool_call`, `tool_result`, `taint`, `plan`, `plan_card`, `plan_decision`, `artifact`, `parked`, `done`, `error`, `learned`, `style_learned`. `_run_chat` publishes each and sets `run.replied` on `done`. — [app.py `_chat_stream`, `_run_chat`](backend/personal_os/app.py)
- Reasoning tokens are buffered in `rbuf`, streamed as `reasoning`, stored on the message, and kept out of `buf`. `Conversations.history` selects `role, content` only, so reasoning is not sent back on the next turn. — [app.py `_chat_stream`](backend/personal_os/app.py); [repos.py `history`](backend/personal_os/repos.py)

**Shipped — model picker, effort, fast**

- `ChatControls` renders `ModelMenu` under the composer on the chat page and in a canvas chat window. The model is the conversation’s `model`, or `draftModel`, or `settings.defaultModel`. Changing it `PATCH`es the conversation, or parks `draftModel` when there is no row yet. — [ChatControls.tsx](src/renderer/src/components/ChatControls.tsx); [store.ts `setChatModel`](src/renderer/src/store.ts)
- `GET /models` calls `llm.list_models`, which `GET`s `{baseUrl}/models` and returns sorted `{id}` rows. Listings also record a vision flag when the provider sends one. — [app.py `models`](backend/personal_os/app.py); [llm.py `list_models`](backend/personal_os/llm.py)
- The turn’s model is `body.model or conv["model"] or settings.defaultModel`. A body model that differs from the conversation is written back. — [app.py `_chat_stream`](backend/personal_os/app.py)
- `stream_chat` POSTs `{base}/chat/completions` with `stream: true` and `stream_options.include_usage`. The module docstring says any OpenAI-compatible API; a LiteLLM proxy is one of them. Empty `baseUrl` raises “No AI provider is set up yet.” — [llm.py](backend/personal_os/llm.py)
- Effort is `conv.settings.effort` (default `"default"` in the call, which omits `reasoning_effort`). The `effort_param` docstring says new chats start at low via `repos.DEFAULT_EFFORT`. Kimi K2 omits the field. Kimi K3 maps low→low, medium→high, high→high, xhigh/max→max. Other models send the effort string as-is. — [llm.py `effort_param`](backend/personal_os/llm.py)
- Fast mode sends `service_tier: "priority"` only when `supports_service_tier` is true, which is provider id `openai`, `litellm`, or `custom`. — [llm.py `stream_chat`, `supports_service_tier`](backend/personal_os/llm.py)
- Before the first byte, 408/425/429/500/502/503/504/529 and transport errors retry (`llmRetries`, default 3, cap 10). Backoff is exponential with jitter, floored at `Retry-After`, capped at 30s; a `Retry-After` over 60s is not waited out. After the first byte, failures are reported and not retried. Idle default is 300s. Stop during backoff raises `LLMError("Stopped…")`. — [llm.py `_send_with_retry`](backend/personal_os/llm.py)

**Shipped — stop and regenerate**

- Stop calls `POST /messages/{mid}/stop` once an assistant id exists, else `POST /conversations/{id}/stop`. Both set the run’s stop event. The local `AbortController` is not the stop. — [store.ts `stop`](src/renderer/src/store.ts); [app.py `stop_message`, `stop_run`](backend/personal_os/app.py)
- A set cancel closes the provider socket. `stream_chat` then ends with `finish_reason: "cancelled"` and drops any partial tool calls. Remaining calls in that round get a tool message that they were not executed. The `done` payload has `stopped: true`. — [llm.py `stream_chat`](backend/personal_os/llm.py); [app.py `_chat_stream`](backend/personal_os/app.py)
- Regenerate is a button under the transcript when the last message is an assistant and this chat is not streaming. It `POST`s chat with `{}`. `ChatIn.content` defaults to `None`, which deletes the trailing assistant message (`removed_message`) and replies to the last user message. It refuses when there is no user message, and it no-ops in the client when a reply is still answering. — [ChatView.tsx](src/renderer/src/components/ChatView.tsx); [store.ts `regenerate`](src/renderer/src/store.ts); [app.py `ChatIn`, `_chat_stream`](backend/personal_os/app.py)

**Shipped — projects and what a turn puts in the prompt**

- A project row has `name`, `description`, `system_prompt`, `color`, `tools`. Conversations are created with a project id and a model. Listing can filter by project. Deleting a conversation trashes it. — [repos.py `Projects`](backend/personal_os/repos.py); [app.py conversation routes](backend/personal_os/app.py)
- `newChat(projectId)` parks `draftProjectId`. The empty chat state says “New chat in {project.name}” when a project is selected. — [store.ts `newChat`](src/renderer/src/store.ts); [ChatView.tsx](src/renderer/src/components/ChatView.tsx)
- `build_context` puts the global system prompt, then `You are currently working in the project "…"`, the description, and `project.system_prompt`, into the stable parts. Query-dependent blocks (page, memories, graph, document excerpts, activity, meetings) go in `volatile_blocks`. Approved skills and the style profile stay stable. Page detail is clipped at 6000 characters and selection at 2000. `estimate_tokens` is `len//4`. — [context.py `build_context`](backend/personal_os/context.py)
- With `cacheLayout` default true, the system message is stable text plus render/tool/job/desk/plan hints plus `_today_hint()`. Volatile blocks are a second system message inserted immediately before the newest user message. `stable_hash` (12 hex chars of sha256) is stored on the context span. `cacheLayout` false concatenates everything into one system message. — [app.py `_chat_stream`](backend/personal_os/app.py); [context.py `layout_messages`](backend/personal_os/context.py); [llm.py `DEFAULT_SETTINGS`](backend/personal_os/llm.py)
- `_today_hint()` is day-granular (weekday, date, zone, next 7 days) and is joined into that stable prefix, not the volatile block. The comment says a clock would break the prefix cache. — [app.py `_today_hint`](backend/personal_os/app.py)
- Tool modes for the turn are `toolbox.effective(global tools, project tools, conversation tools)` when `useTools` is not false. — [app.py `_chat_stream`](backend/personal_os/app.py)
- The first user message in a chat titled “New chat” renames it from the first 48 characters. — [app.py `_title_from`](backend/personal_os/app.py)
- After `done`, auto-learn is `learner.submit` (a background worker) when enabled, the reply is non-empty, the run is not proposal-only, and the turn is not tainted. Style sampling can still run inside the same generator after `done` and append a `style` span via `set_trace`. — [app.py `_chat_stream`](backend/personal_os/app.py)

**Partial**

- The page agent (`sendToPageAgent`) is a second composer path. It sends `page_context` and can steer the same way. That block is volatile context, not a project instruction. — [store.ts `sendToPageAgent`](src/renderer/src/store.ts); [context.py `page_block`](backend/personal_os/context.py)
- `tools_hint` is baked into the system message once, at the start of the reply. Later in the same reply, plan mode and MCP search can change `tool_schemas` for the next `stream_chat` call without rewriting that hint. — [app.py `_chat_stream`](backend/personal_os/app.py)
- A `done` event is yielded before the style span is added. The persisted trace is updated afterward with `set_trace`. A client that only keeps the `done` payload can miss the style span until reload. — [app.py `_chat_stream`](backend/personal_os/app.py)

**Absent**

- There is no conversation branch or fork. Regenerate deletes the last assistant row of this conversation. It does not copy history. — [app.py `_chat_stream`](backend/personal_os/app.py)

### Inferences

- The UI never reads a raw provider socket. If the window closes, the run continues and a later `attachSession` can tail it from `GET /runs` while `answering` is still true. — [store.ts `attachSession`](src/renderer/src/store.ts)
- Because `replied` flips on `done` and the style pass is still inside the generator, a new turn can be accepted while that pass is still calling the model. — [app.py `chat`, `_run_chat`, `_chat_stream`](backend/personal_os/app.py)

### Gaps

- `wsid` / `sid` (how a sentinel project id becomes null) were not read line by line. Conversation create does pass `project_id` through `wsid`. — [app.py `create_conversation`](backend/personal_os/app.py)
- `PRICE_TTL` for the price cache was not recorded.
- No runtime check that a real provider stream matches the SSE event list above.

## How does the tool-calling loop work?

### Takeaway

`_chat_stream` is a sequential tool loop: each round streams one completion, then runs that round’s tool calls one after another. The provider may return several calls in one completion. Parallel execution exists for read-only subagent calls and for read-only `agent_spawn` prestart, not for the parent chat loop. Rounds, tokens, wall clock, and dollars are capped by `Budget`. Usage rows record cache and reasoning tokens; the in-run cost cap does not use the cache price.

### Cited Findings

**Shipped — round shape**

- The loop is `while True` in `_chat_stream`. Each round microcompacts, drains background-shell notes, moves the plan block to the last message (`_reinject_plan`), then calls `llm.stream_chat` with the current tool schemas. — [app.py `_chat_stream`](backend/personal_os/app.py)
- `stream_chat` assembles parallel tool-call fragments by `index`, or by a new `id` when `index` is missing. The end event is `{finish_reason, tool_calls: [{id, name, arguments}], usage, usage_est}`. — [llm.py `stream_chat`, `_slot`](backend/personal_os/llm.py)
- No calls, and no pending steer, ends the loop. The assistant turn (content plus `tool_calls`) is appended, then each call is executed, then the loop continues. — [app.py `_chat_stream`](backend/personal_os/app.py)
- Truncated or non-object tool arguments are replayed to the provider as `{}`. Unparseable arguments become `{_raw: ...}` for the tool side. The comment says a truncated JSON string would 400 the next round. — [app.py `_replay_args`, `_call_args`](backend/personal_os/app.py)
- Default caps: `maxToolRounds` 25, `maxRunTokens` 200_000, `maxRunSeconds` 300, `maxRunCost` 0.50. `0` means unlimited. `Budget.rounds` is set to rounds already completed so the Nth round’s tools are still allowed. Approval wait time is subtracted from the wall clock. — [llm.py `DEFAULT_SETTINGS`](backend/personal_os/llm.py); [app.py `Budget`](backend/personal_os/app.py)
- At 60% of the tightest axis, one system message (`SOFT_NUDGE`) is appended. Crossing 100% sets `partial` to `rounds|tokens|time|cost`, answers every pending call with `BUDGET_STOP`, and runs `_final_round`. An approved plan that covers every call in the round lifts that stop for a non-desk chat; the next round is still checked. — [app.py `_chat_stream`](backend/personal_os/app.py)
- `_final_round` is one more `stream_chat` with `tool_choice: "none"`. It is exempt from the budget and capped by `FINAL_ROUND_SECONDS` (90). `arm_deadline` also bounds a normal stream by remaining `maxRunSeconds`. A provider timeout with no text raises `LLMError`; with text, `partial` is `"time"`. — [app.py `Budget.arm_deadline`, `_final_round`](backend/personal_os/app.py); [llm.py `stream_chat`](backend/personal_os/llm.py)
- Scheduled jobs use tighter caps (`maxToolRounds` 8, 60_000 tokens, 240s, $0.20) and `asyncio.wait_for(..., 1800)` around the whole run. — [app.py `JOB_BUDGET`, `_run_job`](backend/personal_os/app.py)

**Shipped — sequential parent, limited parallel elsewhere**

- The parent executes `for c in calls` and `await`s each `_call_tool` (or MCP call) before the next. Stop skips the rest of the round with a “not executed” tool message. — [app.py `_chat_stream`](backend/personal_os/app.py)
- Before that loop, `subagent_mgr.prestart` starts read-only `agent_spawn` calls together when the round is not in plan mode and autonomy is not `"ask"`. The parent still records each call in order. Non-readonly spawns wait for their own turn. — [subagents.py `prestart`](backend/personal_os/subagents.py); [app.py `_chat_stream`](backend/personal_os/app.py)
- A child’s own loop batches consecutive read-only calls (`danger` in `safe` or `network`, mode `on`, not a writer, not `agent_*`) with `asyncio.gather`, at most `ROUND_PARALLEL` (8) at a time. Identical `(tool, args)` in one round run once and share the result. Anything else is a barrier. — [subagents.py `_run_calls`, `_parallel_ok`](backend/personal_os/subagents.py)

**Shipped — errors and loop breaks**

- A tool result dict with `error` increments a per-tool consecutive counter. At `TOOL_ERROR_LIMIT` (3) the tool is added to `blocked` and later calls in this reply get a denial that it was disabled. A success resets the counter. There is no automatic re-call of a failed tool inside the loop; the model sees the error content. — [app.py `_chat_stream`](backend/personal_os/app.py)
- Five identical consecutive `call_key(name, args)` (`REPEAT_LIMIT`) sets `partial` to `"loop"` before the approval gate. Remaining calls in the round get `LOOP_STOP`, then `_final_round`. — [app.py `_chat_stream`](backend/personal_os/app.py)
- `StuckDetector` is on when `stuckDetection` is true (the default). It watches executed calls and flags: same tool+args+result 4 times; same call erroring 3 times; A/B alternation for 6 observations with unchanged results; one tool erroring 4 times with different args. Window is 24. The first hit appends `STUCK_NUDGE` and clears observations. The second ends tool use through `partial="loop"` and `STUCK_STOP`. — [stuck.py](backend/personal_os/stuck.py); [app.py `_chat_stream`](backend/personal_os/app.py); [llm.py `DEFAULT_SETTINGS`](backend/personal_os/llm.py)
- Exceptions from `toolbox.call` on side-effecting tools are stored as journal status `error` and re-raised. The chat `except` turns an uncaught exception into `done.error` and closes open spans with `tracer.fail_open`. — [runs.py `call_once`](backend/personal_os/runs.py); [app.py `_chat_stream`](backend/personal_os/app.py)
- Large tool JSON is replaced for the model by a handle from `tool_results.for_model`. Images are popped off the result, shown in the UI, and not sent back as image bytes. — [app.py `_chat_stream`](backend/personal_os/app.py)

**Shipped — traces**

- `Tracer.start(kind, name, meta, parent)` stores `parent_id` when `parent` is set. The context span is opened first. Each LLM round span is the parent of that round’s tool spans. History-compact and microcompact spans parent to the context span. Kinds used on a chat reply include `context`, `compact`, `llm`, `tool`, and `style`. Auto-learn spans are appended later by the worker through `set_trace`. — [trace.py `Tracer`](backend/personal_os/trace.py); [app.py `_chat_stream`](backend/personal_os/app.py)
- Spans are yielded as `span` SSE events and stored on the message by `finish_message(..., tracer.spans)`. `summary()` sums prompt, completion, cached, and reasoning tokens from `meta.usage`. — [trace.py](backend/personal_os/trace.py); [repos.py `finish_message`](backend/personal_os/repos.py)
- `TraceView` indents a span whose `parent_id` is in the same trace and shows cached and reasoning counts on the LLM row. — [TraceView.tsx](src/renderer/src/components/TraceView.tsx)
- After `finish_message`, `otel_export.export_in_background` runs. `otelExport.enabled` defaults to false. — [app.py `_chat_stream`](backend/personal_os/app.py); [llm.py `DEFAULT_SETTINGS`](backend/personal_os/llm.py)

**Shipped — tokens and cost**

- `parse_usage` keeps `prompt_tokens`, `completion_tokens`, `total_tokens`, and normalizes `cached_tokens` (OpenAI `prompt_tokens_details.cached_tokens` or Anthropic `cache_read_input_tokens`), `cache_write_tokens`, and `reasoning_tokens`. — [llm.py `parse_usage`](backend/personal_os/llm.py)
- Every `stream_chat` calls `_emit_usage`. If the provider omits usage, tokens are `chars//4` and `estimated` is true; cache and reasoning are stored as 0 on estimated rows. `usage_est` is always on the end event. Reasoning characters are included in the completion-char estimate. — [llm.py `stream_chat`, `_emit_usage`](backend/personal_os/llm.py)
- `_record_usage` writes `usage_log` with conversation id, project id, duration, cost, and the three extra token columns. Cost uses `Pricing.cost(..., cached, cache_write)`. Cache-read and cache-write prices come from the proxy `/model/info` fields `cache_read_input_token_cost` and `cache_creation_input_token_cost` (converted to $ per million). Missing cache prices fall back to the input price. Reasoning tokens are not priced separately; they sit inside completion tokens. — [app.py `_record_usage`](backend/personal_os/app.py); [usage.py `Pricing.cost`](backend/personal_os/usage.py)
- `Usage.report` exposes `cache_hit_rate`. `UsageView` shows “Cache hit rate” and “Reasoning tokens” tiles. — [usage.py `report`](backend/personal_os/usage.py); [UsageView.tsx](src/renderer/src/components/UsageView.tsx)
- The loop charges `budget.add(pt, ct, pricing.cost(cfg, model, pt, ct))` with no cached or cache-write counts. An unpriced model adds `0.0` and does not trip the cost axis. — [app.py `_chat_stream`, `Budget.add`](backend/personal_os/app.py)
- `PUT /usage/prices` saves only `input` and `output` per model, then reprices the log. `Pricing.table` can read `cache_read` / `cache_write` from overrides if they are already in settings. — [app.py `put_prices`](backend/personal_os/app.py); [usage.py `Pricing.table`](backend/personal_os/usage.py)

**Partial — where the loop calls other systems**

- Before a call runs, the loop resolves mode through `_gate`, filesystem ask, plan mode, desk autonomy, `permrules.resolve`, proposal-only jobs, and `skip_permissions` (`permrules.lift_permission_ask`). Ask waits on an approval row with no timeout in an ordinary chat. That policy is another note; the loop is the caller. — [app.py `_chat_stream`](backend/personal_os/app.py)
- Side-effecting tools (`danger` in `writes` or `external`) go through `RunStore.call_once`. Read-only tools call `toolbox.call` directly. — [app.py `_call_tool`](backend/personal_os/app.py)

**Absent**

- The parent loop does not `asyncio.gather` ordinary read-only tools in one round. — [app.py `_chat_stream`](backend/personal_os/app.py)
- `stream_chat` does not send `cache_control` or any other explicit cache breakpoint. Caching is prefix order plus whatever the provider does automatically. — [llm.py `stream_chat`](backend/personal_os/llm.py)
- Usage `kind` is the caller’s string (`chat` by default, `compact` for the summarizer, `learn` for extraction). There is no per-round or per-tool usage row. — [llm.py `stream_chat`](backend/personal_os/llm.py); [compaction.py `compact`](backend/personal_os/compaction.py)

### Inferences

- A cache hit can show a discounted cost in `usage_log` and still burn `maxRunCost` at the full input price, because `Budget` never passes `cached_tokens`. — [app.py `_chat_stream`](backend/personal_os/app.py); [app.py `_record_usage`](backend/personal_os/app.py)
- `maxToolRounds` 25 is “25 rounds whose tool calls are allowed,” then a tool-free closing call if the next round is over budget. The counter is completed rounds, not “stop before round 25.” — [app.py `Budget`, `_chat_stream`](backend/personal_os/app.py)

### Gaps

- `Toolbox.call`’s own exception-to-`tool_error` path was not re-read for this note. The loop’s handling of a returned `error` field and of exceptions from `call_once` is cited above.
- Whether the Settings price form can send `cache_read` was not checked. The save route drops anything but `input` and `output`.

## What is durable about a run?

### Takeaway

The durable record is the `agent_runs` row plus the `run_events` tape, approval rows, and an idempotency journal for side-effecting tools. Resume starts a new run with a tape-derived note; it does not restore the in-memory tool transcript. Compaction summarizes old messages without editing them. There is no branch of a conversation.

### Cited Findings

**Shipped — tape and recovery**

- `RunStore` persists `agent_runs`, `run_events`, `approvals`, and `executed_calls` on one SQLite connection with `synchronous=NORMAL`. `publish` appends a row per event. — [runs.py `RunStore`](backend/personal_os/runs.py)
- `Run` fields that survive include status, `message_id`, budget snapshot, error, `last_seq`, `ended_at`, `resumed_from`, `input`, `desk_id`, `turn`, `parent_run_id`. Status values used in this path include `running`, `awaiting_approval`, and `interrupted`. — [runs.py `Run`, `RunStore.update`](backend/personal_os/runs.py)
- Startup `_recover_runs` calls `recover()`. Active runs that are not live in this process are marked `interrupted`, with an `error` event. Pending approvals stay pending. If the assistant message is still empty, `transcript()` rebuilds text from `delta` events and tool events from `tool_result` events and `finish_message`s that row. A message that already has content or tool events is left alone. — [app.py `_recover_runs`](backend/personal_os/app.py); [runs.py `recover`, `transcript`](backend/personal_os/runs.py)
- `run_events` for runs whose `ended_at` is older than `EVENTS_RETAIN_S` (14 days) are deleted. The comment says user content is not what this prune targets. — [runs.py `EVENTS_RETAIN_S`, `recover`](backend/personal_os/runs.py)

**Shipped — idempotency**

- `idempotency_key` is sha256 of `run_id`, step (the round number), tool name, and `args_digest`. `call_once`: `done` returns the recorded JSON and does not call again; `started` returns `unknown_outcome` and does not call again; `error` runs again. `CancelledError` leaves the row `started`. — [runs.py `call_once`, `unknown_outcome`](backend/personal_os/runs.py)
- Only tools whose spec `danger` is `writes` or `external` use that journal. A replay sets `replayed: true` on a dict result and can re-set taint. — [app.py `_call_tool`, `IDEMPOTENT_DANGER`](backend/personal_os/app.py)
- Resume passes `inherit=resume_of`. `prior_call` looks up the same tool and digest on the old run at any step. `done` is copied onto the new key; `started` returns `unknown_outcome`; `error` falls through and runs. — [runs.py `call_once`, `prior_call`](backend/personal_os/runs.py); [app.py `_call_tool`](backend/personal_os/app.py)

**Shipped — resume**

- `POST /runs/{run_id}/resume` is user-initiated. `resume.resumable` allows it only when status is `interrupted`, kind is `chat`, `desk_id` is empty, the conversation is not answering, this run is the latest for the conversation, and no run already has `resumed_from` pointing here. Otherwise 409 `{reason}`. Success starts `ChatIn(resume_of=run_id)` and sets `resumed_from`. — [app.py `resume_run`, `_resumable`](backend/personal_os/app.py); [resume.py `resumable`](backend/personal_os/resume.py)
- The resume branch does not delete the salvaged assistant message. It sets `user_text` to the last user message and appends a system note from `build_resume_note` (partial text, finished tool results, `started` calls told not to repeat, approvals still pending or approved). `taint_from_tape` only adds taint sources. — [app.py `_chat_stream`](backend/personal_os/app.py); [resume.py `build_resume_note`](backend/personal_os/resume.py)
- The UI shows Resume on an assistant message whose `error` starts with `Interrupted:` when `GET` of the interrupted run says `resumable`. — [Message.tsx `ResumeButton`](src/renderer/src/components/Message.tsx); [store.ts `resumeRun`](src/renderer/src/store.ts)

**Shipped — compaction, not transcript edits**

- `prepare_history` runs before the assistant row. It uses `history_rows` (non-empty `role, content, id, created_at`). When `autoCompact` is true (default), message count exceeds `compactKeepRecent + 2` (default 8+2), and estimated history plus system tokens exceed `compactAt * contextWindow` (0.7 × 128_000), `Compactor.compact` summarizes with the extraction model (or the chat model). The summary is one `conv_summaries` row. Replay is first user message, a synthetic user message `[Summary of earlier conversation]`, then the tail. The tail is snapped back to a user message. Summarizer failure logs and sends the full history. — [compaction.py](backend/personal_os/compaction.py); [app.py `_chat_stream`](backend/personal_os/app.py); [repos.py `history_rows`](backend/personal_os/repos.py)
- The messages table is not edited by compaction. `DELETE /conversations/{id}/summary` drops the summary. `POST /conversations/{id}/compact` accepts `{focus}` and forces a summary. `GET /conversations/{id}/context-meter` returns window, estimate, threshold, and summary. — [compaction.py `router`](backend/personal_os/compaction.py)
- `ContextDrawer` shows a meter and a “Compact now” button, and can show the summary. — [ContextDrawer.tsx](src/renderer/src/components/ContextDrawer.tsx)
- `microcompact` runs at the top of each round. Past `microAt` (0.5) of the window, tool messages older than the newest `microKeep` (3), at least 400 characters, and before the in-flight tool-call turn, are replaced in the in-memory `messages` list with a stub that keeps `result_id` when the content was a handle. A compact span is emitted when anything was cleared. — [compaction.py `microcompact`](backend/personal_os/compaction.py); [app.py `_chat_stream`](backend/personal_os/app.py)

**Shipped — what the next turn does not replay**

- `history` and `history_rows` drop rows with empty `content`. They do not select `tool_events`, `trace`, or reasoning. Prior turns’ tool calls exist on the message row and on the tape, and they are not reconstructed into `role: tool` messages for the next turn. Within one run, those tool messages exist only in the `messages` list in `_chat_stream`. — [repos.py `history`](backend/personal_os/repos.py); [app.py `_chat_stream`](backend/personal_os/app.py)
- The plan block is re-read from storage and re-appended every round (one slot, moved). That is the durable “where am I” note inside a run, separate from compaction. — [app.py `_reinject_plan`](backend/personal_os/app.py)

**Absent**

- No time-travel fork. Regenerating does not keep the discarded assistant message as an alternate branch. — [app.py `_chat_stream`](backend/personal_os/app.py)
- Resume does not rehydrate the dead run’s in-memory tool transcript. The model gets the note, the ordinary (possibly compacted) history, and the idempotency inherit path. — [app.py `_chat_stream`](backend/personal_os/app.py); [resume.py](backend/personal_os/resume.py)
- Chat resume is not automatic on startup. `recover` only marks and salvages. — [app.py `_recover_runs`](backend/personal_os/app.py); [resume.py module docstring](backend/personal_os/resume.py)

**Partial**

- Desk turns are the same `_chat_stream` with `run.desk_id` set. A desk can chain another turn (`_desk_supervisor`) and can resume from `interrupted` via `_wake_desk`. That is a different resume from `POST /runs/{id}/resume`, which rejects desk runs. — [app.py `_run_desk`, `_wake_desk`, `resume.resumable`](backend/personal_os/app.py)
- Workflows have their own `POST /workflow-runs/{run_id}/resume`. That is not the chat tape. — [app.py `resume_workflow_run`](backend/personal_os/app.py)
- If regenerate deletes the message a summary used as its boundary, `Compactor._tail_start` falls back to `created_at`. — [compaction.py `_tail_start`](backend/personal_os/compaction.py)

### Inferences

- After a crash, the user can read the salvaged assistant text and tool cards, and Resume can tell the model about them. The model cannot page the old in-run tool handles unless those handles were stored in `tool_results` and the note or a later call names them. — [app.py `_recover_runs`](backend/personal_os/app.py); [compaction.py `CLEARED_NOTE`](backend/personal_os/compaction.py)
- Idempotency does not make the whole agent replayable. A read that succeeded is not journaled, so a resumed run can read it again. A write that finished is replayed; a write that was `started` is refused. — [app.py `_call_tool`](backend/personal_os/app.py)

### Gaps

- The exact `agent_runs` column list in `db.py` was not re-read. The fields above are the ones `RunStore.create` / `update` write.
- Retention of `usage_log` (`retainUsageDays` 365) and traces (`retainTraceDays` 60) is configured in settings and was not traced through the sweeper.

## Where do the README and the two sota notes disagree with the code?

### Takeaway

`docs/research/sota-agentloop.md` and `docs/research/sota-context.md` describe a loop that does not resume, does not compact, does not detect stuck patterns, and does not record cache tokens. The working tree implements those. The README’s feature list matches the basic chat controls and under-describes the loop. A few gaps in those notes are still true: no conversation fork, no parallel read-only tools in the parent loop, no `cache_control` breakpoints, and the in-run dollar cap ignores cache price.

### Cited Findings

**README — matches the code**

- Streaming chat, a per-chat model picker, regenerate, and stop exist, as the features list says. — [README.md Features](README.md); [Composer.tsx](src/renderer/src/components/Composer.tsx); [ChatControls.tsx](src/renderer/src/components/ChatControls.tsx)
- Projects group chats and carry instructions that are layered into the prompt. — [README.md Features](README.md); [context.py `build_context`](backend/personal_os/context.py)
- “How a reply is built” is right that global prompt then project description and instructions come first, that tools run for up to `maxToolRounds`, that ask-mode can pause, and that auto-learn runs after the reply on `GET /events`. — [README.md “How a reply is built”](README.md); [app.py `_chat_stream`](backend/personal_os/app.py)
- Usage rows store model, kind, conversation, project, tokens, latency, and cost. Missing provider usage is estimated from characters and flagged. Prices come from the proxy `/model/info`, with manual overrides that reprice history. — [README.md “Usage and cost”](README.md); [usage.py](backend/personal_os/usage.py)

**README — underclaims**

- The reply section does not mention steer, durable runs, resume, compaction, microcompaction, stuck detection, cache layout, the today line, skills, style, page context, or the `style` span after `done`. All of those are in `_chat_stream` / `compaction.py` / `stuck.py`. — [README.md “How a reply is built”](README.md); [app.py `_chat_stream`](backend/personal_os/app.py)
- The trace section lists span kinds `context`, `llm`, `tool`, and `learn`. The code also emits `compact` and `style`, stores `parent_id`, and can export OTLP when `otelExport.enabled` is on (default off). — [README.md Traces](README.md); [trace.py](backend/personal_os/trace.py); [app.py `_chat_stream`](backend/personal_os/app.py)
- The usage section does not mention `cached_tokens`, `cache_write_tokens`, or `reasoning_tokens`. Those columns are written and shown in `UsageView`. — [README.md “Usage and cost”](README.md); [usage.py `record`](backend/personal_os/usage.py); [UsageView.tsx](src/renderer/src/components/UsageView.tsx)
- The README says chat goes through LiteLLM. `llm.py` sends an OpenAI-compatible chat-completions request to `settings.baseUrl`. LiteLLM is the intended default proxy, not a special case inside `stream_chat`. — [README.md opening](README.md); [llm.py module docstring](backend/personal_os/llm.py)
- The chip example `4 steps · 6.1 s · 3.7k tok` is illustrative copy in the README, not a measured value from this tree. — [README.md Traces](README.md)

**sota-agentloop.md “Where we are” — stale**

- It places `_chat_stream` at about lines 1000–1600. The function starts at line 1380 and the loop continues through the `done` yield near 2392. — [sota-agentloop.md](docs/research/sota-agentloop.md); [app.py `_chat_stream`](backend/personal_os/app.py)
- It says there is no detection of alternating calls, same-result loops, or error cycles. `StuckDetector` implements those four patterns and `_chat_stream` uses it by default. — [sota-agentloop.md](docs/research/sota-agentloop.md); [stuck.py](backend/personal_os/stuck.py)
- It says there is no mid-run context compaction in `app.py`. Each round calls `compaction.microcompact`, and a cleared round emits a `compact` span. — [sota-agentloop.md](docs/research/sota-agentloop.md); [app.py `_chat_stream`](backend/personal_os/app.py)
- It says a dead chat run stays dead and only desks resume. `POST /runs/{run_id}/resume`, `ChatIn.resume_of`, and the Message Resume button exist for kind `chat`. Desk runs are explicitly rejected by `resumable`. — [sota-agentloop.md](docs/research/sota-agentloop.md); [app.py `resume_run`](backend/personal_os/app.py)
- It says the in-reply tool transcript exists only in memory and `history()` replays content only. That part still matches: resume does not put `role: tool` messages back, and `history()` is still content-only. The tape and the resume note are the extra record the note says is missing. — [sota-agentloop.md](docs/research/sota-agentloop.md); [repos.py `history`](backend/personal_os/repos.py)
- Gap row “dead chat run cannot be resumed” and the loop-1 spec are implemented in `resume.py`, `call_once(inherit=…)`, and the route the spec names. — [sota-agentloop.md loop-1](docs/research/sota-agentloop.md); [resume.py](backend/personal_os/resume.py)
- Gap row “loop detection is only 5 identical consecutive calls” is only half true. `REPEAT_LIMIT` is still 5. The stuck detector is additional and default-on. — [sota-agentloop.md Gaps](docs/research/sota-agentloop.md); [app.py `REPEAT_LIMIT`](backend/personal_os/app.py)
- Gap row “no mid-run context compaction” matches neither `microcompact` nor `prepare_history`. — [sota-agentloop.md Gaps](docs/research/sota-agentloop.md); [compaction.py](backend/personal_os/compaction.py)
- Gap row “read-only tool calls in one round run sequentially” still matches the parent `for c in calls` loop. It does not mention subagent batches of 8 or `agent_spawn` prestart. — [sota-agentloop.md Gaps](docs/research/sota-agentloop.md); [subagents.py `_run_calls`](backend/personal_os/subagents.py)
- Gap row “time travel / fork” still matches. Regenerate still drops only the last assistant message. — [sota-agentloop.md Gaps](docs/research/sota-agentloop.md); [app.py `_chat_stream`](backend/personal_os/app.py)
- The note says local file writes have no pre-image. `_call_tool` calls `snaps.before` when `snaps.wants(...)`, and `FileSnapshots` is constructed in `app.py`. The snapshot policy itself is outside this note. — [sota-agentloop.md](docs/research/sota-agentloop.md); [app.py `_call_tool`](backend/personal_os/app.py)

**sota-context.md “Where we are” — stale**

- It says `Conversations.history` is called at `app.py:1028`, returns every message with no summary, and that a grep for compact finds nothing. The turn calls `compaction.prepare_history` at about line 1438. `compaction.py` exists, with `conv_summaries`, manual compact, and a context meter. — [sota-context.md](docs/research/sota-context.md); [app.py `_chat_stream`](backend/personal_os/app.py); [compaction.py](backend/personal_os/compaction.py)
- It says nothing in `stream_chat` reads cached-token counts. `parse_usage` does, and `usage_log` stores them. It also says the stable hints sit after volatile retrieval inside one system message. With `cacheLayout` true (default), stable text is the first system message and volatile blocks sit just before the newest user message. — [sota-context.md](docs/research/sota-context.md); [context.py `layout_messages`](backend/personal_os/context.py); [llm.py `parse_usage`](backend/personal_os/llm.py)
- It says spans are flat with no parent ids and no compaction span. `Tracer.start` takes `parent`, tool spans pass the round span, and compact spans are emitted. `otel_export.py` exists and is off by default. — [sota-context.md](docs/research/sota-context.md); [trace.py](backend/personal_os/trace.py)
- It says `docs/research.md` G36 and G37 have not shipped. This note did not re-read `docs/research.md`. The mechanisms those sota specs name (`compaction.py`, microcompact, cache columns) are in the tree. — [sota-context.md](docs/research/sota-context.md)
- Gap “no history compaction” and gap “no microcompaction” and the ctx-1 spec are implemented, including `POST /conversations/{id}/compact` with `focus` and the drawer meter. — [sota-context.md ctx-1](docs/research/sota-context.md); [ContextDrawer.tsx](src/renderer/src/components/ContextDrawer.tsx)
- Gap “no cached or reasoning token accounting” is implemented in `usage_log` and `UsageView`. The same note’s pricing formula is what `Pricing.cost` does. The in-run `Budget` call site does not pass cache counts, which the spec’s `Pricing.cost` signature would have discounted. — [sota-context.md ctx-2](docs/research/sota-context.md); [app.py `_chat_stream`](backend/personal_os/app.py)
- Gap “flat spans, no OTLP” is implemented as `parent_id` plus opt-in `otel_export`. — [sota-context.md trace-1](docs/research/sota-context.md); [otel_export.py](backend/personal_os/otel_export.py)

**sota-context.md — still accurate, or only partly fixed**

- `estimate_tokens` is still `len//4`. Context sections for memories, graph, and documents still have no token budget of their own. Page text is char-capped. Skills switch to a manifest past `skillsInlineBudget` (6000 characters). — [context.py](backend/personal_os/context.py); [llm.py `DEFAULT_SETTINGS`](backend/personal_os/llm.py); [sota-context.md](docs/research/sota-context.md)
- The today line is in the stable prefix (`hints` joined into `stable`), so the cached prefix changes when the date changes. The ctx-2 spec says to put that line in the volatile block. — [app.py `_chat_stream`](backend/personal_os/app.py); [sota-context.md ctx-2](docs/research/sota-context.md)
- `stream_chat` still does not send `cache_control`. — [llm.py `stream_chat`](backend/personal_os/llm.py); [sota-context.md](docs/research/sota-context.md)
- History still omits prior tool calls. A long chat’s next turn sees assistant prose (or a summary), not the tool transcript. — [repos.py `history`](backend/personal_os/repos.py); [sota-context.md](docs/research/sota-context.md)
- Usage rows are still not tagged by round or tool. — [llm.py `_emit_usage`](backend/personal_os/llm.py); [sota-context.md Gaps](docs/research/sota-context.md)
- `maxToolRounds` default is still 25, which the agent-loop note says “stays that way.” — [llm.py `DEFAULT_SETTINGS`](backend/personal_os/llm.py); [sota-agentloop.md](docs/research/sota-agentloop.md)

### Inferences

- Those two notes are not a description of the tree on 2026-10-02. Their “Where we are” and “Gaps” sections read as the backlog those specs were written against. The specs’ file names (`resume.py`, `compaction.py`, `stuck.py`, `otel_export.py`) now exist and are called from the chat path.
- The README is safe as a short feature list and wrong as an inventory of the loop. A gap comparison that starts from the README will treat resume, compaction, stuck detection, and cache accounting as missing.

### Gaps

- `docs/research.md` G-numbers were not re-checked against the code. The sota-context note’s claim that G36 and G37 are unshipped is unverified here; the code those specs point at is present.
- This note did not diff HEAD against the working tree, so a behavior that exists only in uncommitted edits is not separated from committed code.
- Permission-rule evaluation order, memory contents, document retrieval ranking, mail, and desk policy were left to other notes except where `_chat_stream` calls them.
