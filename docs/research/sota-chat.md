## Chat: verified gaps against open-source peers, and the hardening plan

## 1. Scope and method

This document covers the normal chat feature of the app: the composer, the transcript renderer, the conversation list and data layer, the chat loop in `backend/personal_os/app.py` (`_chat_stream`), the model transport in `llm.py`, and the client store that tails a reply. It does not cover desks, canvas widgets beyond the chat widget that shares the composer, the agent-loop work already described in `sota-agentloop.md` (durable runs, approvals, plans, verified writes), or any non-chat page.

Method, in four steps:

1. Six code maps were written by reading the code in the `chat-hardening` worktree: composer, render, loop, convo, llm, client. Each has an architecture note, a list of capabilities and a list of weaknesses. Nothing was run. Every finding is from reading code, and line numbers are as of this branch.
2. Twelve surveys of open-source chat apps and agent harnesses were taken, mostly from shallow clones read on disk and, for a few, from changelogs and docs. They produced 416 feature records (project, area, mechanism, evidence, value). Counts per area: llm 108, render 83, convo 69, loop 63, composer 58, client 35.
3. Gap candidates were written per area by comparing the code maps with the peer records, then each candidate went through a verification pass that re-read our code and either confirmed it, corrected the proposal, or rejected it. 95 gaps survived (55 P1, 38 P2, 2 P3; 87 effort M, 7 S, 1 L). Kinds: 35 bug, 31 robustness, 18 feature, 11 ui.
4. An implementation plan was built from the verified list: 14 packages in two waves, with one owner per `_chat_stream` region and per shared file. 63 of the 95 gaps are covered by a package, 32 are parked with a reason.

Limits. The peers move fast and some evidence is a changelog line rather than code. Roo Code and a few Cline-family details came partly from memory when a page redirected. A gap marked real means the code claim survived a reading; none was reproduced in a running app. Peer thresholds below are defaults as read on the survey date (2026-10-02) and several are user-configurable.

## 2. How our chat works today

Six areas, taken from the code maps. Paths are relative to the repo root.

### Composer

`src/renderer/src/components/Composer.tsx` holds one local `useState('')` draft, a paperclip and drop zone, a `SmartTextarea` (autosize plus LLM ghost text via `POST /assist/complete`), Stop and Send buttons, and a footer with `PlanModeToggle` and `ChatControls` -> `ModelMenu`. `ChatView.tsx` mounts it unkeyed and `App.tsx:216` mounts ChatView only while `view === 'chat'`. The same component serves canvas chat widgets and the page-agent panel.

`store.ts` `send` (1527-1612) has three paths. On an existing chat whose reply is still answering it calls `api.steer` (`POST /conversations/{id}/steer`). On an idle existing chat it calls `runStream` (`POST /conversations/{id}/chat`), and a 409 adopts the live run and steers into it. On a draft chat it creates the conversation, PATCHes parked `draftEffort` / `draftFast` / taint, then streams; `draftCreate` serialises concurrent first sends. `send` returns a boolean and the composer clears optimistically and restores the text on `false`.

Stop is a server call (`POST /messages/{mid}/stop` or `/conversations/{id}/stop`) that sets `run.stop` and pokes the provider read. Attachments are not message attachments: `attach` uploads each file to the document library (`POST /documents`, 20 MB cap in `extract_text.py`), sets an "untrusted upload" taint and appends a sentence naming the files to the draft. Model, effort and fast live on the conversation row (`PATCH /conversations/{id}`, shallow settings merge in `repos.py:143-153`). Steers are folded in at the top of the round loop (`app.py` 1689-1716), and `Run.answering` gates steer versus new run.

Weak spots: the draft is lost on any chat switch or view change, only the composer box accepts drops, and failed settings writes are swallowed.

### Transcript rendering

`ChatView.tsx` renders every message with `msgs.map` into a memoized `MessageView` (`Message.tsx`). There is no virtualization or paging. A `stick` flag drives scroll-to-bottom when the last message's content length, message count or conversation id changes.

An assistant reply lays out in a fixed order: Reasoning (collapsible plain text), ToolEvents, MarkdownPreview, cursor, then the error bubble, Resume / FilesChanged, and a hover action row (model tag, context chip, Save as skill, trace chip, Copy). User messages are plain pre-wrap text.

`MarkdownPreview.tsx` is the single pipeline: react-markdown 9, remark-gfm, remark-math (`lib/mathBlocks.ts` normalizes `$$`), rehype-katex, rehype-highlight. Its `Pre` override routes fences to ChartBlock, InteractiveBlock, MermaidBlock, HtmlBlock / SvgBlock (sandboxed srcdoc iframe) or a labelled code block with copy. Links and images go through `SAFE_MD` (http(s) only; remote images become links) and `src/main/navigation.ts` hands navigations to the system browser. `ToolEvents.tsx` renders each call as a dedicated card (`toolcards/registry.ts`) or a generic expandable row, with inline approval.

State: `watchRun` consumes SSE and folds each event through the pure `applyEvent`; `mergeConversation` reconciles refetches. The backend accumulates one text buffer and one tool-event list per assistant message and persists them only at `finish_message`. Regenerate deletes the trailing assistant row first. `RootBoundary` is the only error boundary on the chat path.

### Chat loop

`POST /conversations/{id}/chat` (`app.py:2510`) returns 409 if `bus.answering(id)`; otherwise `RunBus.start` (`runs.py:686`) spawns an asyncio task running `_run_chat` -> `_chat_stream` (`app.py` 1343-2308) and returns `{run_id, seq}`. Clients tail `GET /conversations/{id}/stream?since=&run_id=`. Every event goes through `Run.publish`, which writes a `run_events` row and then fans out to an in-memory ring and subscriber queues.

`_chat_stream` persists the user message, builds context (`context.py build_context`, `compaction.prepare_history`), inserts an empty assistant row, then loops: `llm.stream_chat`, then for each tool call in order gate, approval wait, `_call_tool` (journaled by `RunStore.call_once`), and a tool message built by `working.ToolResults.for_model`. Breakers are `Budget`, `REPEAT_LIMIT`, `TOOL_ERROR_LIMIT` and `stuck.StuckDetector`; each ends in a tool-free `_final_round`.

The assistant row is written once by `convos.finish_message` (`repos.py:174`), at the end, on stop, on error and on CancelledError. After a hard crash `_recover_runs` plus `RunStore.recover` / `transcript` mark the run interrupted and rebuild text and tool events from the tape; `POST /runs/{id}/resume` starts a new run with a resume note. Stop sets `run.stop` and pokes `run.wake`, which closes the provider socket. Steer persists a user message, appends it to `run.steers` and pokes; the loop closes the current segment and opens a new assistant message. Later turns replay only message `content`: tool calls and results are never replayed across turns.

### Conversations and data layer

A conversation is one row in `conversations` (id, project_id, title, model, settings JSON, timestamps, `deleted_at`, `deleted_with`) with a flat, linear `messages` table (`db.py:43-65, 531`). `Conversations` in `repos.py:99-206` is the whole data layer: unpaginated `list`, `get` (all messages), `create`, `update` (title / model / shallow settings merge), `add_message`, `finish_message`, hard `delete_message`. Routes are `app.py:1037-1081`; DELETE soft-deletes through `trash.py` (30-day retention). Titles come from `_title_from` (first 48 chars of the first user message). Regenerate hard-deletes the last assistant row. Runs live per conversation in `RunBus`, independent of the row's lifecycle.

In the renderer, `store.ts` holds `conversations` (the full list, refetched on every `done`), `sessions` (loaded conversations, LRU of 12, status and unread in `sessionStatus.ts`) and `focusedConversationId`. The sidebar renders project groups and date-grouped Recents with a client-side title filter and `ChatPulse` dots; `TrashPanel` restores or purges; `ContextDrawer` edits per-chat settings. There is no fork, edit, pin, archive, per-chat export, message search, or cross-window list sync.

### Model transport (llm.py)

`stream_chat` POSTs an OpenAI-compatible `/chat/completions` through a fresh httpx client per call. `_send_with_retry` retries 408/425/429/5xx/529 and transport errors with jittered exponential backoff and Retry-After, but only until response headers arrive. The SSE loop enforces `llmIdleSeconds` per line and the `stream_deadline` ContextVar, yields `delta` and `reasoning` events, assembles tool calls by index, and ends with one `end` event carrying usage plus a chars/4 estimate. `complete` is the non-streaming twin used by compaction and auto-learn. The request body carries only model, messages, tools, tool_choice, reasoning_effort and service_tier.

Context: `build_context` splits a stable system prefix from per-turn retrieval blocks and `layout_messages` puts the volatile block just before the newest user message. `compaction.prepare_history` folds old turns into a rolling summary at `compactAt` x one global `contextWindow`; `compaction.microcompact` stubs old tool results in-run; `working.py` stores large tool results behind `result_id` handles and re-injects the plan last. `Budget` caps rounds, tokens, seconds and cost and arms the stream deadline. Cross-turn history is text only. Usage flows through `llm.on_usage` to `usage.py`; `redact.py` is not on the chat path.

### Client stream and sessions

A reply is a durable backend run, not a request. `send` calls `runStream` (`store.ts:1076`), which POSTs and gets `{run_id, seq}` back at once. `watchRun` (982) tails the stream through `chatStream` (`lib/api.ts:755`, built on a hand-rolled fetch + ReadableStream parser `sseStream`) and folds each event into a per-conversation `ChatSession` with the pure reducer `applyEvent` (651). `Streaming {messageId, runId, abort, answering}` is the live handle; the AbortController only detaches the viewer. A 409 from `/chat` is parsed by `runConflict` and the run is adopted. `attachSession` (1444) joins a run found through `GET /runs`, but `selectChat` (1428) only fetches the conversation.

Backend side, `Run.subscribe` replays past `since`, follows live and sends `: keepalive` every 15 s. After a restart `stream_conversation` serves the stored tape and `_recover_runs` marks the run interrupted. App-wide events (auto-learn, jobs) come from the `/events` topic via `watchBackgroundEvents`. The sidecar is supervised in `src/main/backend.ts`; the renderer reacts in `onBackendState` and shows `BackendBanner` or `BackendFailed`.

## 3. What peers do, by area

Thresholds and defaults are as read in the source or docs. Evidence is a file path inside the project's repository (see Sources for repository names); where the survey only had a changelog line the entry says so.

### Model transport, retry and context (llm)

**Retry and backoff.** Nearly every peer retries transient failures with exponential backoff and jitter, and honours Retry-After. OpenCode and Kilo Code: up to 5 retries, 2 s start, factor 2, 25% jitter, 30 s cap without headers, `retry-after-ms` / `retry-after` (seconds or HTTP date) preferred (`packages/opencode/src/session/retry.ts`). Codex splits two budgets: `request_max_retries=4` for the initial request and `stream_max_retries=5` for a stream that drops mid-response, each capped at 100 (`codex-rs/model-provider-info/src/lib.rs`). DeepSeek-TUI shares one stream re-issue budget of 3 (`stream_max_resumes`, clamp 0..10) across never-opened requests, streams that died before any content, host sleep and mid-stream drops. Goose: 3 retries, 1 s start, 2.0 multiplier, 30 s max, jitter 0.8-1.2. gemini-cli: 10 attempts, 5 s start, 30 s cap, +/-30% jitter, plus a separate stream-level retry (4 attempts) for no-finish-reason, empty or malformed-function-call streams that injects a nudge message. aider caps total retry time at 60 s.

**Classification.** Retrying everything is wrong. Codex's `retry_delay()` returns none (terminal) for context-window-exceeded, quota, usage limit, invalid request, interrupted and auth failures. OpenCode never retries context overflow and routes it to compaction; pi, aider and Cline do the same. Zed normalizes provider failures into categories (PromptTooLarge, Authentication, PaymentRequired, ContentPolicy, RateLimit) and parses the token count out of the message. DeepSeek-TUI retries a transient error frame hidden inside a 200 response (such as an empty-response frame) while nothing has streamed yet, but not auth or invalid-model frames. LobeChat only falls back to another provider before any visible output; its router buffers an attempt until it produces something, so output from two providers is never mixed.

**Timeouts.** DeepSeek-TUI separates an open timeout (connect plus headers, 45 s, clamp 5..300, with one retry over HTTP/1.1 on an HTTP/2 header stall) from a per-chunk idle timeout (900 s, clamp 1..3600). Codex's `stream_idle_timeout_ms` defaults to 300000 and a silent stream becomes a retryable error. crush wraps non-streaming calls with a per-attempt deadline and times streaming calls out only when the provider sends nothing for the whole window, so a slow but live response is never killed. AnythingLLM binds the abort signal to the SDK client so Stop tears down the upstream request and token billing stops.

**Context overflow and compaction.** The common shape is a proactive threshold plus a reactive path.
- Thresholds: Goose 80% of the window (`GOOSE_AUTO_COMPACT_THRESHOLD`, off at <=0 or >=1); DeepSeek-TUI 80% (range 10-100); Zed 90%; Roo Code a configurable percent (5-100) with a forced reduction to 75% on overflow; gemini-cli 50% of the model limit, keeping the newest 30%; qwen-code `min(85%, window - 13k)` with a warn tier 20k earlier and a hard tier 3k before the end, summary output clamped to the remaining window minus a 1,024 margin; Chatbox `floor(max(window-32000, window*0.5) * 0.6)` with a 128k assumption for unknown models; pi `contextTokens > contextWindow - 16384` keeping 20k recent tokens; crush a 20k buffer above 200k windows and 20% below.
- Reserve: OpenCode `usable = input limit - min(20000, model max output)` and a tail budget of 25% of usable clamped to 2k-15k tokens.
- Reactive: on a context-overflow error Goose shows "Context limit reached. Compacting to continue conversation..." and retries with a capped attempt counter that resets on success; pi and Codex allow exactly one compact-and-retry per run via a one-shot flag so it cannot loop. pi also checks between tool turns, not only before prompts.
- Cheap relief first: OpenCode and Kilo Code walk back through tool output, protect the latest 40k tokens (`PRUNE_PROTECT`) and prune older ones only if that saves at least 20k. gemini-cli protects 50k tokens, masks only when 30k is prunable, replaces output with a `<tool_output_masked>` tag and offloads the full output to disk. Chatbox stubs old tool results at 0.8x of the compaction threshold with "Old tool result cleared to save context space. Call the tool again if this result is needed."
- Safety: DeepSeek-TUI saves the original history durably before replacing context and aborts if that write fails; Roo Code tags condensed messages (`condenseParent`) instead of deleting so an undo restores them; compaction must never keep a tool result whose call was summarized away (DeepSeek-TUI changelog; Continue keeps tool pairs). Zed and Codex re-attach the recent user messages (80,000 bytes / 20,000 tokens) after the summary.
- Meter agreement: DeepSeek-TUI fixed a context meter inflated by about half by making the status bar and the compaction gate read one estimator.

**Reasoning handling.** DeepSeek-TUI replays `reasoning_content` on every assistant message in thinking-mode tool loops (the API rejects a message missing the field) and sanitizes the wire payload with a placeholder. For generic OpenAI-compatible providers it renders answer text that arrives in `reasoning_content` as normal text. It also freezes the system prompt and tool catalog and appends volatile facts rather than splicing them into the prefix, for prompt-cache hits (`docs/CACHE.md`).

### Chat loop, stop, steer and resume (loop)

**Loop detection.** Peers detect more than identical consecutive calls. OpenCode and Kilo Code raise a `doom_loop` permission ask after 3 identical calls (`DOOM_LOOP_THRESHOLD=3`, `processor.ts`). Roo Code compares canonical stable-stringified call JSON, limit 3, with a user prompt. crush hashes each step's calls with their results over a sliding window of 10 steps and stops when one signature occurs more than 5 times. gemini-cli has three tiers: 5 identical calls, 50-character streamed-text chunks hashed and seen 10 times in a cluster, and an LLM check. qwen-code adds periodic verbatim repetition, thought repetition at 3, file-read churn (8 in a window of 15), alternating A/B and a per-turn tool cap. OpenHands' `StuckDetector` has five patterns (same action and observation 4+ times, same action erroring 3+, monologue, A/B alternation for 6 cycles, repeated context-window errors) and injects a single nudge per error streak before a hard stop. Cline and Roo count consecutive mistakes by typed reason (default 6 and 3) and escalate to the user.

**Malformed tool calls.** OpenCode retries with a lower-cased tool name, otherwise rewrites the call to an `invalid` tool whose result carries the schema or parse error, so the model sees what was wrong. Chatbox tries raw, code-fence stripped, trailing commas removed, outermost `{...}` slice, then partial-JSON parse, accepting only an object. Roo Code repairs mismatched tool-result ids and merges consecutive same-role messages before sending. Goose retries an empty turn up to 3 times, then adds a visible fallback message rather than ending silently. Cline appends a conciseness nudge and retries up to 3 times when a turn ends at the output-token limit.

**Stop, interrupt, abort.** OpenCode closes every in-flight tool part as an error with `metadata.interrupted=true` and finalises open reasoning and text parts. LibreChat persists the partial content on abort and shows a cancelled marker. DeepSeek-TUI revokes a turn's pending approvals on cancel so a late approval click cannot resume it, and has a stall watchdog that ends a wedged turn so the next message is accepted. OpenCode keeps one runner per session with a busy guard; mutating operations (revert) fail with BusyError while running.

**Steering and queues.** Goose drains a per-session steer queue between tool rounds. LibreChat caps its steer queue at depth 10. Codex restores unsent steers to the composer on interrupt (or resubmits them as one turn). Zed, Chatbox (max 20, persisted), Open WebUI and Goose keep a queue with Send now. crush's queue carries an accept sequence so ordering survives a cancel.

**Persistence and resume.** Open WebUI saves the in-progress response on every flushed delta; on reload, if the last assistant message is not done and no task is active, it closes it out. DeepSeek-TUI writes a crash checkpoint during a turn and resumes from it. LibreChat's generation job manager buffers events emitted before a subscriber attaches (5000 events / 8 MB), keeps subscriber leases (30 s TTL, refreshed every 10 s) and keeps idempotency keys about 25 hours. Zed's `Message::Resume` renders as "Continue where you left off"; Open WebUI's Continue re-sends history up to the cut-off message with `continueResponse:true` and appends to the same message.

### Conversations: branching, edit, search (convo)

**Edit, regenerate, rewind.** Zed's `truncate(message_id)` cancels the in-flight turn, drops the partial message, removes that message and everything after it with its usage record, and clears the cached summary. DeepSeek-TUI rewinds both the model context and the saved session before the replacement inference. Codex's Esc-Esc backtrack reverts the thread and puts the prompt back in the composer. gemini-cli's rewind offers conversation and code separately and hides the code options when no files changed. OpenCode and Kilo Code mark a revert point without deleting (`session.revert`), refuse while busy, and show a Redo banner.

**Branches.** Open WebUI (`parentId` / `childrenIds` / `currentId`), LibreChat (`parentMessageId` plus an "n / m" sibling switcher), Jan (`activeChildId`), Chatbox (`messageForksHash`) and LobeChat keep alternatives as siblings, so regenerate and edit-and-resubmit never overwrite. Fork into a new chat: OpenCode (`Session.fork`, "X (fork #1)"), Open WebUI (from any finished reply, works on interrupted ones), LibreChat (three scopes: direct path, include branches, up to target level), pi (`/tree` stays in one session file; `/fork` and `/clone` create new sessions), qwen-code (refused while streaming or awaiting confirmation).

**Titles.** OpenCode runs a hidden small-model agent only while the title is still the default. Goose generates after 3 user messages and never overwrites a user rename. Open WebUI asks a task model for `{title}` JSON, slices first `{` to last `}`, falls back to reasoning content and then to the first 100 characters of the user message.

**Search, export, organisation.** Goose runs SQL search over messages and returns snippets grouped by session, also exposed to the agent as a recall tool. Kilo Code's in-transcript search scans only user prompts and assistant text (tool output, reasoning and errors bury results). Cherry Studio's global search debounces with IME awareness. LibreChat exports five formats (markdown, text, json, csv, png), includes branches optionally, and imports Claude, ChatGPT and Chatbot UI exports. Open WebUI's chat menu has pin, rename, clone, move to folder, archive, share, download. Roo Code's history view has fuzzy search, sort by cost or tokens, and batch delete.

### Transcript rendering (render)

**Streaming render cost.** Goose re-renders the streaming message at most once per 50 ms (leading edge plus a trailing flush). big-AGI decimates updates to 15/s on desktop and 12/s on mobile with a minimum 20 ms gap and one free pass after the first chunk. Kilo Code coalesces part deltas per animation frame and flushes control messages first to preserve order. Codex's markdown stream collector commits only completed lines and leaves the partial line buffered. Chatbox coalesces streaming persistence writes: per key at most one write in flight and one latest pending.

**Scrolling.** LibreChat's follow mode attaches within 150 px of the bottom, detaches once the user is more than 24 px away, and resize-follow works within 120 px, with a floating scroll button. Open WebUI coalesces scroll work into one `requestAnimationFrame` and has a setting for scrolling during generation. Chatbox starts following only after a real at-bottom signal, so restoring a session mid-way does not jump, and honours reduced motion. LobeChat uses a virtualized list with scroll position persisted per topic and a back-to-bottom control.

**Errors.** big-AGI pattern-matches errors to guidance (insufficient quota links to usage, invalid key points to key settings). Chatbox classifies a failed message as quota, context-limit, OCR, API (provider and HTTP status), network (host) or unknown, shows the request id and replaces gateway HTML error pages with a clean message. LobeChat's context-overflow card has a single Compact button that compresses and retries the parent message. LibreChat shows a notice when the tool round cap is hit. LobeChat's overloaded card auto-retries up to 5 times with backoff 2, 5, 10, 20, 30 s +/-20% and shows "attempt N / 5".

**Context meter.** Zed: a 16 px ring that turns warning-colored at 85% with a tooltip of used/max, input and output limits, and cost. Roo Code: three segments (used, reserved for output, available) plus a manual condense button. big-AGI: direct, history and reserved-response segments in the composer.

**Message actions and tool cards.** LobeChat registers per-message actions (copy, edit, regenerate, continue, delete, delete-and-regenerate, branching). LibreChat moves focus to the regenerated reply and groups consecutive tool calls. LobeChat's reasoning block stores a duration and shows it after streaming ends. Goose shows status indicator, arguments and inline approval on each tool card.

### Composer (composer)

**Queue versus steer while a reply runs.** This is the most uniform finding: Goose, Open WebUI, Roo Code, LobeChat, LibreChat, Zed, Chatbox, Jan, Codex, gemini-cli, pi and OpenCode all let the user type during a run. OpenCode has a `followup` setting of `steer` or `queue`. pi: Enter queues a steering message delivered after the current turn's tool calls; Alt+Enter queues a follow-up delivered once the agent is idle. gemini-cli joins queued messages with a blank line and submits them as one when idle. Chatbox persists the queue (max 20) so a crash does not lose it and delivers one per finished generation by default. Queue UIs show editable, removable rows above the input; Goose adds drag reorder and pauses on interrupt; Open WebUI and Zed have Send now, which stops the current reply first. DeepSeek-TUI double-tap Enter sends all queued follow-ups into the running turn.

**Drafts.** AnythingLLM syncs the unsent prompt to localStorage scoped to workspace and thread, and keeps reasoning effort per thread.

**Recall and keys.** OpenCode recalls prompts with Up only at the very start of an empty box and Down only at the end, capped at 100 entries. Open WebUI: Up on an empty input edits the last message; Escape stops generation; one `shortcuts.ts` registry feeds a cheatsheet (`mod+/`). big-AGI: Ctrl+Shift+Z regenerates, Ctrl+L opens the model menu.

**Token budget in the composer.** big-AGI's progress bar stacks direct (draft plus attachments), history and reserved response against the context limit and turns warning-colored near the limit.

### Client stream, reconnect and sessions (client)

**Reconnect and resume.** LibreChat reconnects a dropped stream up to 5 times with `min(1000 ms * 2^(attempt-1), 30 s)`, gives the start call its own 3 network retries and a 120 s timeout, and re-attaches on page load or tab return by replaying buffered content then tailing live events. DeepSeek-TUI frames carry a durable cursor and the server's final frame carries the close reason and a resume cursor. Our own `?since=` tail is already in this family.

**Visible retry state.** OpenCode sets a session status `{type:'retry', attempt, message, next}` so the client can show a live countdown. Codex emits "Reconnecting... N/max" and suppresses the first transient retry. Zed shows "Retrying. Next attempt in N seconds (Attempt X of Y)." and hides it at zero. big-AGI uses per-class backoff profiles (net-disconnect base 500 ms max 8 s; 429/502/503 base 1 s max 30 s; other 5xx base 200 ms max 2 s) and never retries client-aborted requests. Codex, on transport disconnect, preserves composer input and reconciles which submissions were confirmed after reconnect.

**Stale-stream guards.** Jan keeps a monotonic stream generation so a terminal error or finish from a superseded request cannot clear the state of a newer one. Open WebUI closes out an interrupted generation on reload.

## 4. Verified gaps

Each gap was re-read against our code before it was kept. Tags are `<area>-<n>`. Priority P1 is a data-loss, hang or wrong-output class; P2 is a degraded behaviour or a feature that peers all have; P3 is polish. Kind is bug, robustness, feature or ui. Effort is S, M or L as estimated at verification. The last column is the plan package that closes it, or "parked".

| tag | pri | kind | effort | title | area | package |
|---|---|---|---|---|---|---|
| loop-1 | P1 | bug | S | Approval ids collide across rounds, so a later tool call inherits an earlier decision with no click | loop | reply-outcome, stop-and-delete |
| loop-2 | P1 | bug | M | Stop does not stop tools, and a hung tool pins the chat forever | loop | stop-and-delete |
| loop-3 | P1 | feature | M | Earlier turns' tool calls and results are invisible to the model | loop | context-and-history |
| loop-4 | P1 | robustness | M | finish_reason is ignored: truncated, filtered and empty replies are saved as complete | loop | llm-stream-core, reply-outcome, transcript-notices, chat-loop-hardening-2 |
| loop-5 | P1 | robustness | M | Malformed tool calls: no argument repair, raw garbage echoed to the provider, unknown names reported as 'turned off' | loop | chat-loop-hardening-2 |
| loop-6 | P1 | robustness | M | A reply that ended in error, stop or truncation cannot be continued, and regenerate repeats its side effects | loop | reply-outcome, transcript-notices |
| loop-7 | P1 | feature | M | No queued follow-ups; steer always cuts the answer and does nothing during an approval wait | loop | chat-loop-hardening-2 |
| loop-8 | P1 | feature | M | No edit-and-resend or rewind to an earlier user message | loop | edit-and-resend |
| loop-9 | P1 | robustness | M | Regenerate destroys the old answer before a replacement exists | loop | regenerate-variants |
| loop-10 | P1 | bug | M | The pre-loop phase is outside the stop and error envelope | loop | parked |
| loop-11 | P1 | robustness | M | Stopped and partial outcomes are not persisted; a user-stopped run ends as 'done' | loop | reply-outcome, transcript-notices, chat-loop-hardening-2 |
| loop-12 | P1 | bug | S | Deleting a conversation or message does not stop its live run, and message delete is unscoped | loop | stop-and-delete |
| loop-13 | P2 | robustness | M | No message-order or role-alternation repair before a request is sent | loop | parked |
| loop-14 | P1 | robustness | M | Context window is one global estimate with no recovery from a real overflow | loop | context-and-history |
| loop-15 | P2 | robustness | M | The reply is persisted only at the end, and every streamed token is a synchronous SQLite commit on the event loop | loop | parked |
| loop-16 | P2 | robustness | M | In-band provider errors are never retried, and cancelled or failed rounds are not billed to the budget | loop | llm-retry-transport |
| llm-1 | P1 | robustness | M | Context overflow is fatal, and every model shares one 128k window | llm | context-and-history |
| llm-2 | P1 | bug | M | A reply that runs out of wall-clock mid-loop stops silently, and `partial` is never shown | llm | reply-outcome, transcript-notices |
| llm-3 | P1 | robustness | M | Retries stop at response headers: an error frame inside a 200, or a drop before the first token, is never retried | llm | llm-retry-transport |
| llm-4 | P1 | bug | M | finish_reason 'length' and 'content_filter' are ignored, and an empty round becomes a blank bubble | llm | reply-outcome, chat-loop-hardening-2 |
| llm-5 | P1 | robustness | M | Stop and the run deadline are ignored while waiting for headers and during compaction; no open timeout | llm | llm-retry-transport |
| llm-6 | P1 | robustness | M | Provider errors are not classified: quota 429s are retried, overflow / unsupported-parameter / content-filter 400s read as raw text | llm | llm-stream-core, reply-outcome, transcript-notices |
| llm-7 | P2 | ui | M | Retries, backoff and compaction are invisible: the chat shows thinking dots for minutes | llm | parked |
| llm-8 | P1 | bug | M | reasoning_effort is sent to every model with no capability check or retry without it | llm | model-and-action-feedback, llm-retry-transport |
| llm-9 | P2 | robustness | M | The compaction trigger and the context meter use different, low estimates and ignore provider-reported tokens | llm | context-and-history |
| llm-10 | P1 | bug | M | Inline <think> blocks are stored and replayed as answer text; non-string content crashes the reply | llm | llm-stream-core |
| llm-11 | P2 | feature | M | Later turns see no trace of earlier tool work, and a failed round cannot be resumed | llm | reply-outcome, context-and-history |
| llm-12 | P2 | robustness | M | Compaction can re-run every turn, has no failure breaker, an unbounded input, and a stale summary after delete | llm | context-and-history |
| llm-13 | P2 | bug | M | Usage is lost when a stream fails or is abandoned, and cost is blank outside a LiteLLM proxy | llm | parked |
| llm-14 | P2 | robustness | M | Reasoning and provider tool-call fields are dropped between tool rounds | llm | parked |
| llm-15 | P2 | bug | M | The run budget prices rounds differently from the usage log, and long chats burn the token axis on the prompt | llm | parked |
| llm-16 | P1 | robustness | M | Tool-call delta assembly trusts `index`, and malformed pieces are replayed to the provider | llm | llm-stream-core, chat-loop-hardening-2 |
| client-1 | P1 | bug | M | Classic chat view never attaches to a live run it did not start (reload, pop-out, other window) | client | attach-live-run |
| client-2 | P1 | bug | M | Backend crash or pre-reply failure: the terminal `error` event never reaches the transcript (no Interrupted marker, no Resume, no Retry) | client | reply-outcome, attach-live-run, transcript-notices, stream-settle |
| client-3 | P1 | bug | M | Attach starts at the run's current seq: the reply's beginning is missing, the 409 path can paint nothing, and a race duplicates the tail permanently | client | attach-live-run |
| client-4 | P2 | bug | M | A stream that closes without `done` or `error` is treated as a finished reply while the run keeps executing | client | stream-settle |
| client-5 | P2 | robustness | M | Reconnect is a blind 27.5s that retries non-retryable errors, is not coordinated with the backend supervisor, and shows no progress | client | parked |
| client-6 | P1 | robustness | M | No coalescing of token deltas; the markdown pipeline re-runs in full and remounts rich blocks on every token | client | render-perf-boundary |
| client-7 | P1 | robustness | M | No stall watchdog and no request timeouts: 'working' can last forever and Stop/Send can hang | client | stream-settle |
| client-8 | P1 | robustness | M | No error boundary below the root: one bad message or tool card replaces the whole app | client | render-perf-boundary |
| client-9 | P1 | bug | S | Deleting a chat (or its project) mid-reply does not stop the run; closing an attached session refetches it | client | stop-and-delete |
| client-10 | P1 | bug | M | Regenerate deletes the old reply before the new one exists | client | regenerate-variants |
| client-11 | P2 | ui | M | Errors and cut-offs carry no machine-readable class, and the error UI has no actions or retry status | client | llm-stream-core, transcript-notices |
| client-12 | P2 | ui | M | No optimistic user message and no pending state between click and run start | client | parked |
| client-13 | P1 | ui | M | A reply that finishes or needs approval in the background is effectively invisible | client | stream-settle |
| client-14 | P1 | bug | M | init() has an uncaught data load that leaves a blank transparent window; action rejections vanish | client | model-and-action-feedback |
| client-15 | P2 | robustness | S | Stop swallows failures and gives no feedback | client | stream-settle |
| client-16 | P1 | bug | M | Composer draft is shared across chats and lost on view change | client | composer-drafts |
| render-1 | P1 | bug | S | Code/chart/mermaid/html blocks remount on every streamed token (inline `pre` component) | render | render-perf-boundary |
| render-2 | P1 | bug | M | Regenerate hard-deletes the previous answer before a replacement exists; no versions | render | regenerate-variants |
| render-3 | P1 | robustness | M | Each token re-parses the whole reply and re-renders tool cards, ChatView and page context | render | transcript-notices, render-perf-boundary |
| render-4 | P1 | bug | M | A stream that ends without `done` leaves the open chat stale (dots forever, no error, no Resume) | render | stream-settle |
| render-5 | P1 | bug | M | Auto-scroll follows only text length; no jump-to-latest; stick state leaks across chats and ignores own sends | render | transcript-notices |
| render-6 | P2 | robustness | M | A run that fails before the assistant message exists leaves no inline error and no retry; nothing shows during context assembly | render | regenerate-variants |
| render-7 | P2 | bug | M | Stopped and cut-off replies look complete; Stop before first token leaves an empty bubble | render | reply-outcome, transcript-notices |
| render-8 | P1 | bug | M | Prices render as math and LaTeX bracket delimiters do not render | render | render-perf-boundary |
| render-9 | P1 | bug | M | Attaching to a run in progress shows only the tail of the reply | render | attach-live-run |
| render-10 | P1 | feature | M | Order of text, tool calls and reasoning inside a reply is lost; reasoning has no duration | render | parked |
| render-11 | P2 | ui | M | Long tool runs are a wall of rows; collapsed rows hide errors and show raw milliseconds | render | parked |
| render-12 | P2 | feature | M | Tool output is capped at a 1500-character preview with no way to open the rest | render | parked |
| render-13 | P1 | feature | M | No edit-and-resend for a sent message; single-message delete is unwired and its route ignores the conversation | render | edit-and-resend |
| render-14 | P2 | ui | M | Half-written markdown flashes as literal markers while streaming; wide tables overflow the column | render | parked |
| render-15 | P1 | robustness | M | Long conversations mount everything at once, and one render exception blanks the whole app | render | render-perf-boundary |
| render-16 | P2 | feature | M | No find-in-conversation | render | parked |
| convo-1 | P1 | bug | S | Deleting a chat leaves its reply running (tools, approvals and auto-learn continue on a trashed chat) | convo | stop-and-delete |
| convo-2 | P1 | bug | M | Regenerate hard-deletes the old answer before the new one exists; no way back | convo | regenerate-variants |
| convo-3 | P1 | bug | M | Classic chat page never joins a reply already in flight, and is not refreshed after a backend restart | convo | attach-live-run |
| convo-4 | P1 | bug | M | Composer draft is shared across chats and lost on navigation | convo | composer-drafts |
| convo-5 | P1 | robustness | M | Opening a missing chat leaves a stuck page; conversation actions fail silently | convo | model-and-action-feedback |
| convo-6 | P1 | feature | M | No edit-and-resend or rewind to an earlier message | convo | edit-and-resend |
| convo-7 | P2 | feature | M | No search over what was said in chats (title substring only) | convo | parked |
| convo-8 | P2 | robustness | M | Chat list is unpaginated, refetched whole after every reply, and never pushed between windows | convo | parked |
| convo-9 | P2 | feature | M | Auto-titles are raw 48-character truncations and never improve | convo | parked |
| convo-10 | P2 | feature | M | No fork / duplicate of a conversation from a chosen message | convo | parked |
| convo-11 | P2 | ui | M | A reply that finishes in the background leaves no lasting mark | convo | parked |
| convo-12 | P2 | bug | M | Context toggles on a new chat do nothing, and the tools map is overwritten wholesale | convo | parked |
| convo-13 | P2 | feature | L | No pin, archive, move-to-project or row menu for chats | convo | parked |
| convo-14 | P2 | feature | M | No per-conversation export; whole-app export leaks trashed chats and drops tool activity | convo | parked |
| convo-15 | P2 | ui | M | Chats cannot be opened or switched from the keyboard; no find in transcript | convo | parked |
| convo-16 | P2 | feature | M | No per-chat usage total, and stored message timestamps are never shown | convo | parked |
| composer-1 | P1 | bug | M | Composer draft is one shared useState: lost on any view change, leaks between chats, gone on restart | composer | composer-drafts |
| composer-2 | P1 | bug | M | Dropping a file outside the composer box navigates the app window to the file | composer | composer-drafts |
| composer-3 | P2 | feature | M | A mid-reply message can only interrupt: no queue-until-done, no pending tray | composer | parked |
| composer-4 | P1 | bug | M | Steer races: a steer before the first token is stored below its answer, and a steer during context assembly is sent to the model twice | composer | context-and-history, chat-loop-hardening-2 |
| composer-5 | P2 | robustness | M | Attachments have no chips or pending state: a send can outrun the upload, and the taint and project target can land on the wrong chat | composer | parked |
| composer-6 | P2 | robustness | M | No paste handling and no size bound: pasted screenshots are ignored, huge pastes go straight into the message | composer | parked |
| composer-7 | P1 | bug | S | Images and unreadable files upload 'successfully' but the model gets only a placeholder | composer | composer-drafts |
| composer-8 | P2 | ui | M | Stop has no shortcut, no pending state and leaves no trace | composer | stream-settle |
| composer-9 | P2 | robustness | M | Ghost-text completion calls the LLM on every typing pause with no opt-out, no cancellation and no IME guard | composer | parked |
| composer-10 | P1 | bug | M | Model, effort, fast and plan-mode changes fail silently, and 'Restore defaults' races two PATCHes | composer | model-and-action-feedback |
| composer-11 | P3 | feature | M | No way to recall a previous prompt from the keyboard | composer | parked |
| composer-12 | P1 | robustness | M | Model picker is not capability-aware: non-chat models listed, effort offered to every model, no retry, model not sticky | composer | model-and-action-feedback, llm-retry-transport, stream-settle |
| composer-13 | P3 | ui | M | No context or draft-size indicator near the composer | composer | parked |
| composer-14 | P2 | ui | M | Idle composer is unlabeled, does not hold focus, and the setup notice neither blocks sending nor recognises all keyless endpoints | composer | parked |
| composer-15 | P2 | feature | M | No slash commands or @-references in the composer | composer | parked |

### Gap entries

#### Chat loop

**loop-1** (P1, bug, S): Approval ids collide across rounds, so a later tool call inherits an earlier decision with no click.  
Today: llm.py:575 falls back to id 'call_<idx>' when the provider omits a tool_call id, so every round's first call is 'call_0'. app.py:1880 keys the approval as uid = '<message_id>:<call id>', which is the same in rounds 1 and 2 of one assistant message. runs.py:227-231 open_approval is INSERT ...  
Fix: Make tool-call ids unique per reply at the one place that sees every round, and stop the approval wait from trusting a row it did not open. No renderer change, no schema change, uid format unchanged.  
Peers: Goose dedupes duplicate tool-call ids within one assistant turn (reply_parts.rs ~650-690) so a tool cannot run twice or corrupt history.  
Plan: reply-outcome, stop-and-delete.

**loop-2** (P1, bug, M): Stop does not stop tools, and a hung tool pins the chat forever.  
Today: app.py:1807 `for c in calls` has no stop check, and app.py:2141 `await _call_tool(...)` is never raced against run.stop. Only the approval wait (app.py:1983-1990) and the top of the while loop (app.py:1683) look at stop.  
Fix: Make Stop effective inside the tool phase of `_chat_stream` in `backend/personal_os/app.py`. No per-tool timeout and no watchdog.  
Peers: OpenCode closes every in-flight tool part on interrupt as status=error, 'Tool execution aborted', metadata.interrupted=true (processor.ts ~590-612), and cancel also stops the session's background...  
Plan: stop-and-delete.

**loop-3** (P1, feature, M): Earlier turns' tool calls and results are invisible to the model.  
Today: repos.py:191-206 history()/history_rows() SELECT only role and content WHERE content != ''. compaction.py:136-143 build_history maps rows to {role, content}. Tool calls live only in messages.tool_events (full arguments plus a 1500-char result_preview, app.py:2169-2197) and large blobs in tool_results (working.py:205-226).  
Fix: Replay a compact, deterministic text record of each earlier reply's tool calls inside the history the model sees. No synthetic provider tool messages, no schema change, no budget change.  
Peers: OpenCode stores tool calls as typed message parts (pending/running/completed/error) and replays them; pruned outputs are marked time.compacted rather than dropped.  
Plan: context-and-history.

**loop-4** (P1, robustness, M): finish_reason is ignored: truncated, filtered and empty replies are saved as complete.  
Today: llm.py:507 sends no max_tokens. llm.py:583-584 records finish_reason, but app.py:1754-1755 inspects only 'cancelled' and 'timeout'. On 'length' or 'content_filter' the loop takes `if not calls: break` (app.py:1772-1779) and finish_message writes the cut-off text with error None and partial None.  
Fix: Nothing was run; this spec comes from reading the code.  
Peers: Cline retries a turn cut at the output limit with a conciseness nudge, up to 3 times, and gives content-filter stops a distinct terminal message (agent-runtime.ts L60-100).  
Plan: llm-stream-core, reply-outcome, transcript-notices, chat-loop-hardening-2.

**loop-5** (P1, robustness, M): Malformed tool calls: no argument repair, raw garbage echoed to the provider, unknown names reported as 'turned off'.  
Today: app.py:1263-1269 _call_args: unparseable text becomes {'_raw': ...}, non-object JSON (a double-encoded string, an array) becomes {} with no hint. app.py:1780-1781 still puts the raw c['arguments'] string and c['name'] (possibly '') into the assistant turn, so the next request can be rejected by a provider or proxy that parses arguments,...  
Fix: 1) New pure module backend/personal_os/toolcalls.py (no app imports, unit-testable): - `parse_arguments(raw: str) -> tuple[dict | None, bool, str | None]` returning (args, repaired, problem).  
Peers: Chatbox repairs tool-call JSON by trying raw, fence-stripped, trailing-comma-removed, outermost {...} slice, then a partial-JSON parse, accepting only an object (tool-call-json-repair.ts).  
Plan: chat-loop-hardening-2.

**loop-6** (P1, robustness, M): A reply that ended in error, stop or truncation cannot be continued, and regenerate repeats its side effects.  
Today: resume.py:23-24 allows resume only when status == 'interrupted' (backend died). A provider failure in round 5 ends the run as 'error' (app.py:2238-2239) and /runs/{id}/resume returns 409 (app.py:2825-2836).  
Fix: A.  
Peers: Zed has a Resume message ('Continue where you left off') and a retry button on an errored turn (thread.rs Message::Resume, thread_view.rs retry_generation).  
Plan: reply-outcome, transcript-notices.

**loop-7** (P1, feature, M): No queued follow-ups; steer always cuts the answer and does nothing during an approval wait.  
Today: A send during a live reply is either 409 (app.py:2517-2520) or a steer (app.py:2529-2551). steer_run always calls run.poke(), which closes the provider socket (llm.py:483-486): prose is cut mid-sentence and a tool call being streamed is discarded (llm.py:601-604).  
Fix: Four changes; three are small backend fixes in `_chat_stream` (backend/personal_os/app.py), one is a renderer-only queue.  
Peers: Codex keeps a FIFO of messages typed during a turn and auto-sends them when it ends; 'pending steers' are delivered at the next tool boundary, Esc sends them now by interrupting, and on interrupt...  
Plan: chat-loop-hardening-2.

**loop-8** (P1, feature, M): No edit-and-resend or rewind to an earlier user message.  
Today: The only message routes are DELETE /conversations/{id}/messages/{mid} (app.py:1078-1081) and POST /messages/{mid}/stop (app.py:3447). ChatIn (app.py:1094-1098) supports new content, regenerate-last (content=None) and resume_of.  
Fix: Linear rewind as a hard truncate in one transaction. No schema migration, no revert marker, no unrewind.  
Peers: Open WebUI: inline edit with save-only or save-and-resubmit.  
Plan: edit-and-resend.

**loop-9** (P1, robustness, M): Regenerate destroys the old answer before a replacement exists.  
Today: app.py:1375-1379: regenerate calls convos.delete_message on the trailing assistant row (hard DELETE, repos.py:187-189) and publishes removed_message before any model call. If the provider is down or the key is invalid, the conversation is left with an error stub and the original answer, tool events and reasoning are gone.  
Fix: Goal: a regenerate never destroys the previous answer until a replacement has actually been produced. No version history, no switcher.  
Peers: LibreChat stores regenerations as siblings with an 'n / m' switcher.  
Plan: regenerate-variants.

**loop-10** (P1, bug, M): The pre-loop phase is outside the stop and error envelope.  
Today: After the user message is saved (app.py:1361-1362) the run awaits pricing.refresh (app.py:1388), retrieval (1391-1392) and possibly compactor.compact via prepare_history (app.py:1401-1402; compaction.py:163; llm.complete with a 120s timeout and retries, llm.py:616-620). run.stop is first checked at app.py:1683 and no assistant_message or...  
Fix: Backend, backend/personal_os/app.py.  
Peers: DeepSeek-TUI: Esc during a compaction that serves an in-flight turn stops the turn, not just the compaction pass.  
Plan: parked.

**loop-11** (P1, robustness, M): Stopped and partial outcomes are not persisted; a user-stopped run ends as 'done'.  
Today: repos.py:174-181 finish_message stores content, error, context_used, tool_events, trace, reasoning. `stopped` and `partial` exist only in the done event (app.py:2262-2265) and in run.partial in memory (app.py:2261). runs.py:533-538 Run.end sets status 'error' or 'done'; there is no 'stopped'.  
Fix: Persist one per-message outcome and render it. Leave run statuses alone.  
Peers: LibreChat persists aborted content and shows a cancelled marker.  
Plan: reply-outcome, transcript-notices, chat-loop-hardening-2.

**loop-12** (P1, bug, S): Deleting a conversation or message does not stop its live run, and message delete is unscoped.  
Today: app.py:1072-1075 delete_conversation only calls trash.trash; nothing calls bus.stop (it is called only for desks, app.py:6332-6427).  
Fix: All changes are backend; the renderer needs none.  
Peers: OpenCode keeps one runner per session; mutating operations call assertNotBusy and fail with BusyError, and cancel also stops the session's background jobs (run-state.ts).  
Plan: stop-and-delete.

**loop-13** (P2, robustness, M): No message-order or role-alternation repair before a request is sent.  
Today: History is sent as stored (repos.py:191-197; compaction.py:136-143). Empty assistant rows are filtered out, so an errored or stopped turn yields user,user.  
Fix: Add a pure request-shaping step in `backend/personal_os/llm.py`, applied to the wire copy only. The caller's list is never mutated, because app.py removes the plan block by identity (`m is not plan_msg`, app.py:1634) and `compaction.microcompact` edits the list in place.  
Peers: Roo Code runs mergeConsecutiveApiMessages and validateAndFixToolResultIds before every request.  
Plan: parked.

**loop-14** (P1, robustness, M): Context window is one global estimate with no recovery from a real overflow.  
Today: compaction.py:181-186 compacts when len//4 estimate of history plus system exceeds compactAt x contextWindow (one global setting, default 128000), excluding tool schemas. In-run, only microcompact runs (app.py:1720-1725), against the same global window.  
Fix: Backend only. The renderer needs no change: `TraceView` already renders `compact` spans, and the error rides the existing `done.error`.  
Peers: pi checks projected context after tool results are appended and before the next assistant response, compacting in prepareNextTurn, as well as before a new prompt (compaction.md).  
Plan: context-and-history.

**loop-15** (P2, robustness, M): The reply is persisted only at the end, and every streamed token is a synchronous SQLite commit on the event loop.  
Today: The assistant row is written once by finish_message (repos.py:174) at end, stop, error or cancel; a hard crash relies on _recover_runs rebuilding from the tape (app.py:3009-3029), which restores only delta text and tool_result events for run.message_id and drops reasoning and trace (runs.py:376-387).  
Fix: Three parts. A and B are backend only; C is a small renderer change and can ship separately if a dedicated attach/reconnect item already covers it.  
Peers: Open WebUI saves the in-progress response on each flushed delta (middleware.py ~5000-5010).  
Plan: parked.

**loop-16** (P2, robustness, M): In-band provider errors are never retried, and cancelled or failed rounds are not billed to the budget.  
Today: llm.py:364-411 _send_with_retry retries only on HTTP status or transport errors before headers. A proxy that answers 200 and then sends {'error': ...} as its first data chunk hits `raise LLMError` at llm.py:559-561, and the run fails although nothing was streamed.  
Fix: All changes are backend-only; the `stream_chat` signature and event contract stay unchanged (app.py treats any non-delta/non-reasoning event as `end`, so no new event type). No renderer change: a final failure still arrives as `done.error` and renders as today.  
Peers: Kilo Code replays an incomplete response with the same backoff schedule when no side effects occurred and exposes attempt and next-retry time.  
Plan: llm-retry-transport.

#### Model transport and context

**llm-1** (P1, robustness, M): Context overflow is fatal, and every model shares one 128k window.  
Today: One global `contextWindow` (backend/personal_os/llm.py:123) feeds the compaction trigger (backend/personal_os/compaction.py:180-182), microcompact (backend/personal_os/app.py:1720-1721) and the meter (compaction.py:271-272).  
Fix: A.  
Peers: OpenCode and Kilo Code route a context-overflow error to compaction instead of retry and check overflow against (model input limit - reserved output).  
Plan: context-and-history.

**llm-2** (P1, bug, M): A reply that runs out of wall-clock mid-loop stops silently, and `partial` is never shown.  
Today: `Budget.arm_deadline` floors the next stream at 5s (backend/personal_os/app.py:1248-1254).  
Fix: Three parts: close out a timed-out round with the existing final round, persist the stop reason, and show it. No limit changes.  
Peers: Codex preserves the completed turn and reports status.  
Plan: reply-outcome, transcript-notices.

**llm-3** (P1, robustness, M): Retries stop at response headers: an error frame inside a 200, or a drop before the first token, is never retried.  
Today: `_send_with_retry` returns as soon as the status is below 400 (backend/personal_os/llm.py:397-398), although the settings comment promises retries 'before a reply's first token' (llm.py:140-142).  
Fix: All changes are in backend/personal_os/llm.py. The renderer and app.py are untouched: the retry happens inside the `stream_chat` generator before anything is yielded, so the chat loop (app.py:1660, 1734) and subagents.py:637 benefit unchanged.  
Peers: DeepSeek-TUI has one shared re-issue budget (`stream_max_resumes`, default 3) covering never-opened requests, streams that die before content, and gateway error frames hidden in a 200; auth and...  
Plan: llm-retry-transport.

**llm-4** (P1, bug, M): finish_reason 'length' and 'content_filter' are ignored, and an empty round becomes a blank bubble.  
Today: backend/personal_os/app.py:1754-1759 branches only on 'cancelled' and 'timeout'; the value is otherwise only recorded in the trace span (app.py:1765). A round with no content and no calls hits `if not calls: break` (1772-1779) and `finish_message` stores empty text with error None (2247-2249).  
Fix: All backend changes are in `_chat_stream` (backend/personal_os/app.py). No DB or route change.  
Peers: gemini-cli treats NO_RESPONSE_TEXT, MALFORMED_FUNCTION_CALL and MAX_TOKENS as invalid streams, retries with a nudge appended, and tells the UI to discard the partial. pi treats an early 'length' stop...  
Plan: reply-outcome, chat-loop-hardening-2.

**llm-5** (P1, robustness, M): Stop and the run deadline are ignored while waiting for headers and during compaction; no open timeout.  
Today: `_send_with_retry` awaits `cm.__aenter__()` with nothing racing the cancel event (backend/personal_os/llm.py:375-380); `_close_when` is created only after a response exists (llm.py:532).  
Fix: All behaviour changes are in the backend; the renderer needs no protocol change (a stop already arrives as `done` with `stopped: true`, and `sessionStatus.ts` treats that as done unless `error` is set).  
Peers: DeepSeek-TUI separates an open timeout (connect + headers, 45s, clamp 5..300) from the per-chunk idle timeout (900s) and retries a header stall once.  
Plan: llm-retry-transport.

**llm-6** (P1, robustness, M): Provider errors are not classified: quota 429s are retried, overflow / unsupported-parameter / content-filter 400s read as raw text.  
Today: `describe_http_error` covers 429, 401/403, 404 and 5xx (backend/personal_os/llm.py:325-339). The 429 branch drops `detail` (llm.py:329-331) and every 429 is retried (llm.py:404), so a quota or billing 429 is retried three times and then reported as 'rate-limiting you... wait a moment'.  
Fix: 1. Backend: classify and retry by kind (backend/personal_os/llm.py)  
Peers: Codex's error taxonomy makes ContextWindowExceeded, QuotaExceeded, UsageLimitReached and InvalidRequest terminal and Stream, RateLimit, Timeout, ConnectionFailed retryable.  
Plan: llm-stream-core, reply-outcome, transcript-notices.

**llm-7** (P2, ui, M): Retries, backoff and compaction are invisible: the chat shows thinking dots for minutes.  
Today: Backoff only logs (backend/personal_os/llm.py:393, 407) and `stream_chat` yields nothing before the first SSE line, so three retries with Retry-After up to 60s each look like a hang.  
Fix: Surface provider retries and history compaction as a transient per-message status, using one new run event and no new `stream_chat` parameter.  
Peers: Roo Code emits a retry row every second with a live countdown and checks abort each tick.  
Plan: parked.

**llm-8** (P1, bug, M): reasoning_effort is sent to every model with no capability check or retry without it.  
Today: New chats default to 'medium' (backend/personal_os/repos.py:94) and `effort_param` returns the raw value for everything except kimi-k2 / kimi-k3 (backend/personal_os/llm.py:442-456), including 'xhigh' and 'max' from the picker (src/renderer/src/components/ModelMenu.tsx:8-15).  
Fix: 1. Detect a rejected optional field (backend/personal_os/llm.py)  
Peers: OpenHands retries a request without the optional markers the provider rejected.  
Plan: model-and-action-feedback, llm-retry-transport.

**llm-9** (P2, robustness, M): The compaction trigger and the context meter use different, low estimates and ignore provider-reported tokens.  
Today: Everything is `len(text) // 4` (backend/personal_os/context.py:10-11; backend/personal_os/compaction.py:82-91). `prepare_history` is passed `used['tokens_estimate']` computed before the render / tool / plan / date hints are appended (backend/personal_os/app.py:1401-1402 vs 1575-1589) and never counts the tools array.  
Fix: Goal: one load computation shared by the auto-compaction gate, the in-run stub trigger and the drawer meter. It counts the full request (system + hints + retrieval blocks + tool schemas + history) and is corrected by the provider's own count when one exists.  
Peers: DeepSeek-TUI uses one shared estimator for the status meter and the auto-compact gate: last billed prompt plus growth since, evaluated at the pre-request boundary.  
Plan: context-and-history.

**llm-10** (P1, bug, M): Inline <think> blocks are stored and replayed as answer text; non-string content crashes the reply.  
Today: `delta['content']` is yielded untouched (backend/personal_os/llm.py:570-572) and appended to `buf` (backend/personal_os/app.py:1745-1749).  
Fix: All production changes are in backend/personal_os/llm.py. The renderer and app.py do not change.  
Peers: Chatbox strips think tags from generated summaries.  
Plan: llm-stream-core.

**llm-11** (P2, feature, M): Later turns see no trace of earlier tool work, and a failed round cannot be resumed.  
Today: `history_rows` selects role and content only (backend/personal_os/repos.py:191-206); `tool_events` are stored for the UI and never sent back (comment at backend/personal_os/app.py:1414-1415).  
Fix: Two parts, both text-only so switching models stays safe.  
Peers: DeepSeek-TUI's compaction preserves recent tool exchanges and never keeps a tool result whose call was summarised away. gemini-cli keeps function responses in the preserved tail under a 50k budget...  
Plan: reply-outcome, context-and-history.

**llm-12** (P2, robustness, M): Compaction can re-run every turn, has no failure breaker, an unbounded input, and a stale summary after delete.  
Today: No hysteresis: if first message + summary + last `compactKeepRecent` (8) rows + system still exceed the limit, each turn advances the cut and makes another blocking summariser call, rewriting the summary near the front of the prompt and breaking the prefix cache every turn (backend/personal_os/compaction.py:150-158, 181-186).  
Fix: All backend logic is in backend/personal_os/compaction.py; the transcript (`messages` table) stays untouched and no schema migration is needed.  
Peers: qwen-code stops auto-compaction after 3 consecutive failures, clamps summary output to the remaining window, and has warn / auto / hard tiers.  
Plan: context-and-history.

**llm-13** (P2, bug, M): Usage is lost when a stream fails or is abandoned, and cost is blank outside a LiteLLM proxy.  
Today: `_emit_usage` runs after the `async with` block, not in a finally (backend/personal_os/llm.py:609-610), so an idle timeout, an in-stream error or a transport drop (llm.py:545, 561, 590) leaves no usage_log row and nothing charged to the budget.  
Fix: Five small slices, in priority order. No budget limit changes; nothing about approvals changes.  
Peers: DeepSeek-TUI keeps provider-reported cost verbatim and records unknown pricing as 'unpriced', never as free.  
Plan: parked.

**llm-14** (P2, robustness, M): Reasoning and provider tool-call fields are dropped between tool rounds.  
Today: The assistant turn appended after a tool-calling round is `{role, content, tool_calls: [{id, type, function: {name, arguments}}]}` (backend/personal_os/app.py:1780-1781).  
Fix: Backend only. No renderer, DB or SSE-contract change.  
Peers: DeepSeek-TUI replays `reasoning_content` on every assistant message in thinking-mode tool loops, with a final wire sanitiser that injects a placeholder where it is missing, and shows a chip for...  
Plan: parked.

**llm-15** (P2, bug, M): The run budget prices rounds differently from the usage log, and long chats burn the token axis on the prompt.  
Today: `Budget.add` sums prompt + completion per round (backend/personal_os/app.py:1231-1233) against `maxRunTokens` 200k (backend/personal_os/llm.py:137). A chat at ~80k tokens, just under the 89.6k compaction trigger, spends 80k+ per round, so its third round's tool calls are refused with BUDGET_STOP.  
Fix: Three parts. No limit changes; `Budget.add` keeps summing prompt + completion.  
Peers: DeepSeek-TUI's frozen-prefix discipline keeps the tool-loop prefix cached and attributes every cache miss.  
Plan: parked.

**llm-16** (P1, robustness, M): Tool-call delta assembly trusts `index`, and malformed pieces are replayed to the provider.  
Today: `idx = tc.get('index', 0)` with name and arguments appended by `+=` (backend/personal_os/llm.py:573-582).  
Fix: Three small backend changes; no renderer change, no new event type.  
Peers: gemini-cli treats MALFORMED_FUNCTION_CALL and UNEXPECTED_TOOL_CALL as invalid-stream errors with their own retry. pi keeps synthetic failed tool results for calls cut off by a length stop.  
Plan: llm-stream-core, chat-loop-hardening-2.

#### Client stream

**client-1** (P1, bug, M): Classic chat view never attaches to a live run it did not start (reload, pop-out, other window).  
Today: `selectChat` (src/renderer/src/store.ts:1428-1439) only GETs the conversation; `attachSession` (store.ts:1444-1458) is the only caller of `api.runs()` and is used only by the canvas chat widget and desks; `init` (store.ts:1203-1254) never queries /runs.  
Fix: Two steps. Step 1 is the fix; step 2 keeps the sidebar and an already-open chat current.  
Peers: LibreChat `useResumeOnLoad` re-attaches to an in-flight generation on reload or navigation, replaying buffered content then tailing live.  
Plan: attach-live-run.

**client-2** (P1, bug, M): Backend crash or pre-reply failure: the terminal `error` event never reaches the transcript (no Interrupted marker, no Resume, no Retry).  
Today: `applyEvent` has no `error` case (src/renderer/src/store.ts:701-702 falls to `default: return s`); `watchRun` only toasts it for 6s (store.ts:1043-1045, toast timeout at :1351).  
Fix: 1. Types — src/shared/types.ts:1210 Widen the event to `{ event: 'error'; data: { message: string; interrupted?: boolean; run_id?: string; pending_approvals?: string[] } }`.  
Peers: DeepSeek-TUI turns a failed resume or load into a durable transcript error row instead of a status line the next update erases.  
Plan: reply-outcome, attach-live-run, transcript-notices, stream-settle.

**client-3** (P1, bug, M): Attach starts at the run's current seq: the reply's beginning is missing, the 409 path can paint nothing, and a race duplicates the tail permanently.  
Today: `attachSession` tails from `run.seq` (src/renderer/src/store.ts:1455-1457) and the 409 adopt path from `conflict.seq` (store.ts:1089).  
Fix: Make the run's tape the only source for a live reply in an attached window. Renderer-only; no backend change.  
Peers: LibreChat's resume replays buffered content from the start and then tails live events, with per-run matching so events only affect their own generation.  
Plan: attach-live-run.

**client-4** (P2, bug, M): A stream that closes without `done` or `error` is treated as a finished reply while the run keeps executing.  
Today: `chatStream` returns on any clean EOF (src/renderer/src/lib/api.ts:761-766).  
Fix: Mostly a client fix, plus one small server guard. No protocol change.  
Peers: DeepSeek-TUI sends a final frame carrying the close reason and a resume cursor whenever the server ends a stream, so the client knows whether to reconnect.  
Plan: stream-settle.

**client-5** (P2, robustness, M): Reconnect is a blind 27.5s that retries non-retryable errors, is not coordinated with the backend supervisor, and shows no progress.  
Today: `chatStream` retries any failure 8 times at 0.5s-5s (src/renderer/src/lib/api.ts:720, :767-770) and then throws; `sseStream` throws a bare `Error('401 Unauthorized')` (api.ts:725) and `req` drops the status too (api.ts:59-68), so 401/404 are retried for the full budget and a 409 is detected by JSON-parsing the message (store.ts:606-613).  
Fix: Four small changes. Drop the error-class refactor, the per-chat attempt counter (the app-level restart banner already covers it) and the hello/boot-id frame.  
Peers: openai/codex emits 'Reconnecting...  
Plan: parked.

**client-6** (P1, robustness, M): No coalescing of token deltas; the markdown pipeline re-runs in full and remounts rich blocks on every token.  
Today: Each delta is one `patchSession` -> `set()` with a `msgs.map` over the whole conversation and a string concat (src/renderer/src/store.ts:655-656, :682, :999-1007).  
Fix: Two independent changes; the backend, wire protocol and `applyEvent` stay untouched. Change A fixes the visible defect and can land alone.  
Peers: Kilo Code's `createFrameQueue` batches part-delta messages per animation frame into one reactive batch, flushes before any non-delta control message to preserve ordering, and falls back to a timer...  
Plan: render-perf-boundary.

**client-7** (P1, robustness, M): No stall watchdog and no request timeouts: 'working' can last forever and Stop/Send can hang.  
Today: `sseStream` awaits a bare `reader.read()` with no idle timer and discards the server's 15s `: keepalive` comments (src/renderer/src/lib/api.ts:729-745; backend/personal_os/runs.py:32, :567-570), so the consumer cannot tell silence from a wedged backend. `req` calls `fetch` with no signal (api.ts:50-58).  
Fix: Client-only change; the backend already emits what is needed (`: keepalive` every 15s on both `Run.subscribe` and `Topic.subscribe`).  
Peers: LibreChat gives the start-generation call its own retries and a readiness timeout, and reconnects a dropped stream to the still-running job.  
Plan: stream-settle.

**client-8** (P1, robustness, M): No error boundary below the root: one bad message or tool card replaces the whole app.  
Today: `RootBoundary` is the only boundary on the classic chat path (src/renderer/src/RootBoundary.tsx:16-43). `ChatView` maps messages straight into `MessageView` (src/renderer/src/components/ChatView.tsx:107), which renders ToolEvents, tool cards and MarkdownPreview with its chart/mermaid/html blocks (components/Message.tsx:114-168) unguarded.  
Fix: Add one generic boundary and use it at three levels, placed inside the shared components so every transcript that reuses chat code is covered without touching call sites. ChatView.tsx does not change.  
Peers: Goose wraps the chat in an ErrorBoundary and drives the UI from a typed chat state.  
Plan: render-perf-boundary.

**client-9** (P1, bug, S): Deleting a chat (or its project) mid-reply does not stop the run; closing an attached session refetches it.  
Today: `deleteChat` trashes the row and `closeSession` aborts only this window's SSE viewer (src/renderer/src/store.ts:1475-1482, :1459-1467).  
Fix: Backend, `backend/personal_os/app.py`: 1. `delete_conversation` (line 1072): make it `async def`, as `stop_run` and `delete_desk` are, so the Event is set on the loop.  
Peers: AnythingLLM's ActiveGenerationGuard intercepts leaving a chat while a reply streams and only aborts once the user confirms.  
Plan: stop-and-delete.

**client-10** (P1, bug, M): Regenerate deletes the old reply before the new one exists.  
Today: `regenerate` posts /chat with an empty body (src/renderer/src/store.ts:1676-1681). The backend hard-deletes the trailing assistant row and emits `removed_message` before any model call (backend/personal_os/app.py:1375-1379), and the client filters it out (store.ts:679-680).  
Fix: Make regenerate a two-phase replace: the old reply stays in the database until the new one has produced something, and comes back on screen if it has not.  
Peers: LobeChat keys its retry state to the parent user message so it survives the delete-and-recreate of a failed turn, and keeps Stop working during the wait.  
Plan: regenerate-variants.

**client-11** (P2, ui, M): Errors and cut-offs carry no machine-readable class, and the error UI has no actions or retry status.  
Today: The backend writes actionable sentences per class (backend/personal_os/llm.py:325-349), but `LLMError` is a bare Exception (llm.py:249) and the events carry `error` as a string (src/shared/types.ts:1191, :1210), so the client cannot branch.  
Fix: A. Error class on the backend  
Peers: OpenCode sets session status `{type: 'retry', attempt, message, next}` so the client shows a live countdown, and some errors carry an action (label and link, e.g. open settings) rendered as a button.  
Plan: llm-stream-core, transcript-notices.

**client-12** (P2, ui, M): No optimistic user message and no pending state between click and run start.  
Today: `send` inserts nothing locally (src/renderer/src/store.ts:1527-1554); the user bubble appears only from the `user_message` SSE event (store.ts:661), and status becomes 'working' only inside `watchRun` (store.ts:995), after `await api.chat` (store.ts:1079).  
Fix: Renderer-only change. No backend, API or shared-type changes.  
Peers: openai/codex keeps queued submissions visible, reconciles which were confirmed after a reconnect, and retains uncertain prompts in a recovered queue rather than dropping them.  
Plan: parked.

**client-13** (P1, ui, M): A reply that finishes or needs approval in the background is effectively invisible.  
Today: `unread` is incremented (src/renderer/src/store.ts:674) and exported as `useUnread` (store.ts:3125), but no component reads it; it only protects the session from eviction (src/renderer/src/sessionStatus.ts:57).  
Fix: Renderer-only, plus one settings key. No new backend events.  
Peers: Cherry Studio runs a ConversationNotificationRuntime that raises notifications for conversation events such as a reply finishing in the background.  
Plan: stream-settle.

**client-14** (P1, bug, M): init() has an uncaught data load that leaves a blank transparent window; action rejections vanish.  
Today: After `/health` succeeds, `init` awaits `Promise.all` of settings, projects, stats and conversations outside any try/catch (src/renderer/src/store.ts:1217-1219).  
Fix: All changes are in the renderer; no backend change.  
Peers: Chatbox's session startup recovery coordinator records load failures and offers a retry or safe path on the next launch rather than reopening into the same failure, with all storage access...  
Plan: model-and-action-feedback.

**client-15** (P2, robustness, S): Stop swallows failures and gives no feedback.  
Today: `stop()` awaits the POST with `.catch(() => undefined)` and ignores the body (src/renderer/src/store.ts:1688-1695).  
Fix: Renderer-only change. No backend change: both stop routes exist and are covered by `backend/tests/test_runs.py::test_stop_ends_the_run`.  
Peers: Chatbox keeps a stop-operation state machine keyed per session or message with status idle | stopping | failed; a second Stop click while stopping returns the same in-flight promise, and a failure...  
Plan: stream-settle.

**client-16** (P1, bug, M): Composer draft is shared across chats and lost on view change.  
Today: The draft is a local `useState('')` in Composer (src/renderer/src/components/Composer.tsx:22) and ChatView mounts it unkeyed (src/renderer/src/components/ChatView.tsx:117), so text typed in chat A is still in the box after switching to chat B and can be sent there.  
Fix: Renderer-only; no backend change. Nothing below was run; it is a spec from reading the code.  
Peers: Goose's ChatSessionsContainer keeps several chat instances alive so switching sessions does not drop per-chat queue or input state. openai/codex preserves composer input across a disconnect and keeps...  
Plan: composer-drafts.

#### Transcript rendering

**render-1** (P1, bug, S): Code/chart/mermaid/html blocks remount on every streamed token (inline `pre` component).  
Today: src/renderer/src/components/MarkdownPreview.tsx:93 passes `components={{ ...SAFE_MD, pre: (p) => <Pre .../> }}`, so the element type for every fenced block is a new function per render and React unmounts/remounts it.  
Fix: Renderer-only change; no backend or store changes.  
Peers: OpenCode keeps stable DOM across streaming updates with explicit row reconciliation (timeline/row-reconciliation.ts).  
Plan: render-perf-boundary.

**render-2** (P1, bug, M): Regenerate hard-deletes the previous answer before a replacement exists; no versions.  
Today: backend/personal_os/app.py:1374-1379 calls `convos.delete_message` (plain `DELETE FROM messages`, backend/personal_os/repos.py:187-189, not trash) and emits `removed_message` before context assembly even starts; src/renderer/src/store.ts:679-680 drops it from the session and store.ts:1676-1681 fires regenerate with no confirmation.  
Fix: Soft-supersede the trailing answer on regenerate, keep it reachable through a small prev/next switcher in the existing regenerate row, and put it back automatically when the replacement comes out empty.  
Peers: Chatbox stores siblings in session.messageForksHash with a chevron prev/next switcher (ForkGroup.tsx); LobeChat shows an 'n/m' branch switcher (MessageBranch.tsx) and a pending placeholder during...  
Plan: regenerate-variants.

**render-3** (P1, robustness, M): Each token re-parses the whole reply and re-renders tool cards, ChatView and page context.  
Today: One `patchSession` per SSE event (store.ts:997-1007) with `content: m.content + ev.data.text` (store.ts:681-682); MarkdownPreview.tsx:89-96 then re-runs remark-gfm, remark-math, rehype-katex and rehype-highlight over the full text, so cost is quadratic in reply length.  
Fix: Renderer-only. No backend change: the SSE tape, seq numbering and `?since=` resume stay as they are, and `chatStream` in lib/api.ts keeps tracking seq untouched.  
Peers: Goose throttles the streaming message to one render per 50ms with leading + trailing flush (useThrottledStreamingText.ts).  
Plan: transcript-notices, render-perf-boundary.

**render-4** (P1, bug, M): A stream that ends without `done` leaves the open chat stale (dots forever, no error, no Resume).  
Today: `applyEvent` has no `error` case (store.ts:651-703, default returns `s`); watchRun's `error` handler is only a toast (store.ts:1043-1045) and only attached runs refetch in `finally` (store.ts:1054-1057).  
Fix: Renderer-only fix. A run that ends without a final `done` must settle the open transcript in place, then adopt the persisted row.  
Peers: Open WebUI normalises any error payload into an inline per-message block (Messages/Error.svelte) rather than a toast.  
Plan: stream-settle.

**render-5** (P1, bug, M): Auto-scroll follows only text length; no jump-to-latest; stick state leaks across chats and ignores own sends.  
Today: ChatView.tsx:43-48: the scroll effect depends on `lastLen`, `msgs.length`, `convo?.id`, `stick`. Growth from tool rows, approval cards, tool images, mermaid/chart renders, FilesChanged or Resume never triggers it, so an approval card can sit below the fold while the run waits.  
Fix: Renderer-only change; no backend or store-shape change.  
Peers: LibreChat uses hysteresis (attach within 150px, detach after 24px of user scroll, resize-follow within 120px) plus a floating ScrollButton (useMessageScrolling.ts).  
Plan: transcript-notices.

**render-6** (P2, robustness, M): A run that fails before the assistant message exists leaves no inline error and no retry; nothing shows during context assembly.  
Today: Context assembly and compaction run before the assistant row is created and outside the try block (backend/personal_os/app.py:1387-1410); a failure there reaches the UI only as an `error` event, i.e. a 6s toast (store.ts:1043-1045).  
Fix: Keep the existing event order (`user_message` -> `assistant_message` with `context_used` -> ... -> `done`). Add one new event, one guard, and three small renderer changes.  
Peers: LobeChat fills the gap under the user message with a pending placeholder turn (PendingRetryTurn.tsx) and has typed error cards incl. a context-overflow card with a one-click action...  
Plan: regenerate-variants.

**render-7** (P2, bug, M): Stopped and cut-off replies look complete; Stop before first token leaves an empty bubble.  
Today: `done` carries `stopped` and `partial` (app.py:2262-2265) but the `done` case in applyEvent (store.ts:695-700) copies neither onto the message, `finish_message` (repos.py:174-181) does not persist them, and no component reads `message.partial` (src/shared/types.ts:520-521 says 'not persisted').  
Fix: Persist why a reply ended early and render it. No change to the `done` wire shape, because backend/tests/test_runs.py:236 asserts its exact key set.  
Peers: big-AGI shows a warning-coloured Continue bar on cut-off or interrupted messages (BlockOpContinue.tsx).  
Plan: reply-outcome, transcript-notices.

**render-8** (P1, bug, M): Prices render as math and LaTeX bracket delimiters do not render.  
Today: MarkdownPreview.tsx:84 uses remark-math defaults; the comment at MarkdownPreview.tsx:21-23 claims a price is safe. Running the repo's remark stack on 'It costs $5 and $10 per seat.' yields `inlineMath: "5 and "`, so two dollar amounts in one paragraph become a KaTeX formula.  
Fix: All logic stays render-only in the renderer (stored message content, Copy, and doc bodies are never rewritten); the backend change is one prompt line.  
Peers: Goose and LibreChat pair remark-math/rehype-katex with a preprocessing pass for delimiters; qwen-code supports inline and block LaTeX with a rendered/raw toggle and never hides source on unsupported...  
Plan: render-perf-boundary.

**render-9** (P1, bug, M): Attaching to a run in progress shows only the tail of the reply.  
Today: Assistant content is persisted only by `finish_message` (backend/personal_os/repos.py:174-181; the row is inserted empty at app.py:1410).  
Fix: Replay the current message on attach instead of joining at the tail.  
Peers: OpenCode rebuilds its timeline from a projection of persisted message parts, so a late viewer sees the whole in-flight message.  
Plan: attach-live-run.

**render-10** (P1, feature, M): Order of text, tool calls and reasoning inside a reply is lost; reasoning has no duration.  
Today: Message.tsx:127-134 always draws Reasoning, then all ToolEvents, then all markdown. The backend appends every round's text to one `buf` and every call to one `tool_events` list (app.py:1668-1671, 1744-1749) and the reducer does the same (store.ts:681-688).  
Fix: Keep `content`, `reasoning` and `tool_events` exactly as stored today. Add positional metadata beside them and derive the ordered layout in the renderer.  
Peers: LobeChat groups tool blocks inside the assistant turn in order (AssistantGroup/toolRenderRules.ts) and shows reasoning with duration and a thinking flag, skipping empty reasoning (Reasoning.tsx).  
Plan: parked.

**render-11** (P2, ui, M): Long tool runs are a wall of rows; collapsed rows hide errors and show raw milliseconds.  
Today: ToolEvents.tsx:282-339 renders one row or card per event with no grouping, so a 30-call run puts 30 rows above the answer. A failed generic row shows only a red border and an icon; the error text needs an expand (ToolEvents.tsx:297-309, 336).  
Fix: Four changes. The first three are renderer-only; the fourth adds one field to an existing backend event.  
Peers: qwen-code collapses a finished tool batch into a one-line label and force-expands on error.  
Plan: parked.

**render-12** (P2, feature, M): Tool output is capped at a 1500-character preview with no way to open the rest.  
Today: `summarize_result(limit=1500)` (backend/personal_os/tools.py:1058-1083) produces `result_preview` (app.py:2157), the only result the row, ResultBlock or RawDetails can show (toolcards/parts.tsx:42-84).  
Fix: Backend: backend/personal_os/working.py (`ToolResults`) - Add `UI_PREVIEW_CHARS = 1500`, matching the `summarize_result` default. - Add `keep(conversation_id, message_id, tool, result, over=UI_PREVIEW_CHARS) -> dict | None`.  
Peers: Goose's ToolCallWithResponse shows full response content under each call. pi toggles collapsed/expanded tool output globally. openai/codex guarantees that preview truncation never alters the copied...  
Plan: parked.

**render-13** (P1, feature, M): No edit-and-resend for a sent message; single-message delete is unwired and its route ignores the conversation.  
Today: User messages are plain text with only Copy (Message.tsx:124, 162); store.ts has no edit/truncate action. `api.conversations.deleteMessage` (lib/api.ts:370) has no caller, and the route (backend/personal_os/app.py:1078-1081) deletes by message id without checking it belongs to `{id}` or that no run is answering.  
Fix: Edit-and-resend for user messages in the normal chat, implemented as a soft truncation that runs inside the normal chat run.  
Peers: LibreChat edits user and assistant messages in place, a user edit resubmitting as a sibling (EditMessage.tsx).  
Plan: edit-and-resend.

**render-14** (P2, ui, M): Half-written markdown flashes as literal markers while streaming; wide tables overflow the column.  
Today: MarkdownPreview.tsx:89-96 hands the raw partial source to ReactMarkdown; only `$$` is normalised (lib/mathBlocks.ts). Unclosed ``, backticks, `[text](url`, `$...` and a table whose delimiter row has not arrived render as literal characters and then jump.  
Fix: Renderer-only; no backend, store or API change. Deltas keep flowing through `applyEvent` case `'delta'` (`store.ts:681`) into `message.content`, then `Message.tsx:130` passes `streaming` to `MarkdownPreview`.  
Peers: openai/codex commits only completed lines (markdown_stream.rs) and holds back a pipe-table header until the delimiter row arrives, fence-aware (table_holdback.rs).  
Plan: parked.

**render-15** (P1, robustness, M): Long conversations mount everything at once, and one render exception blanks the whole app.  
Today: ChatView.tsx:107 maps every message into MessageView with no virtualization, paging or content-visibility (grep finds `content-visibility` only in styles/canvas.css:415). `Conversations.get` loads every message with its tool_events and trace JSON (backend/personal_os/repos.py:122-131).  
Fix: Renderer-only change; no backend or API change. `Conversations.get` and `message.trace` stay as they are.  
Peers: LibreChat mounts an initial window of 16 rows for threads of 40+ and fills the rest in transitions (useProgressiveRowMount.tsx), and wraps markdown in MarkdownErrorBoundary.  
Plan: render-perf-boundary.

**render-16** (P2, feature, M): No find-in-conversation.  
Today: grep for findInPage / found-in-page across src returns nothing; src/main/index.ts:168 uses the stock editMenu, so Cmd+F does nothing in a transcript. Messages have no DOM id to jump to (Message.tsx:120), and the sidebar filter matches titles only.  
Fix: Renderer-only find bar for the classic chat transcript; no backend or preload change.  
Peers: Goose has an in-conversation SearchBar/SearchView with highlighting.  
Plan: parked.

#### Conversations

**convo-1** (P1, bug, S): Deleting a chat leaves its reply running (tools, approvals and auto-learn continue on a trashed chat).  
Today: DELETE /conversations/{id} only calls trash.trash and ignores its bool, always returning ok:true (backend/personal_os/app.py:1072-1075; backend/personal_os/trash.py:63-71). The client then closeSession()s, which aborts only its own viewer (src/renderer/src/store.ts:1459-1467, 1475-1482).  
Fix: Backend, backend/personal_os/app.py  
Peers: Qwen Code refuses branch/fork while streaming or awaiting a tool confirmation; Kilo Code's revert refuses when the session is busy; DeepSeek-TUI makes fork leave a running turn untouched.  
Plan: stop-and-delete.

**convo-2** (P1, bug, M): Regenerate hard-deletes the old answer before the new one exists; no way back.  
Today: The regenerate branch of _chat_stream calls convos.delete_message(last assistant id) and yields removed_message as its first step, before context build or any model call (backend/personal_os/app.py:1374-1384; backend/personal_os/repos.py:187-189).  
Fix: Keep the linear messages table; regenerate hides the old reply instead of deleting it, a failed regenerate puts it back, and a successful one leaves both switchable.  
Peers: Open WebUI and Jan keep a message tree (parentId/childrenIds or activeChildId) so regenerate adds a sibling with an 'n/m' switcher.  
Plan: regenerate-variants.

**convo-3** (P1, bug, M): Classic chat page never joins a reply already in flight, and is not refreshed after a backend restart.  
Today: selectChat only GETs the conversation (src/renderer/src/store.ts:1428-1439); attachSession, which looks up GET /runs and tails the run, is called only from the canvas widget (store.ts:1444-1458; src/renderer/src/canvas/widgets/chat.tsx:123-129).  
Fix: 1. Backend: expose where the in-flight segment starts (backend/personal_os/runs.py) - Add `self.segment_seq = 0` to `Run.__init__`. - In `Run.publish`, when `event == "assistant_message"`, set `self.segment_seq = self.seq - 1`.  
Peers: LibreChat keeps a running-conversation indicator fed by the server (running.ts) so any view knows a chat is live.  
Plan: attach-live-run.

**convo-4** (P1, bug, M): Composer draft is shared across chats and lost on navigation.  
Today: The draft is one `useState('')` inside Composer (src/renderer/src/components/Composer.tsx:22). ChatView mounts Composer unkeyed (ChatView.tsx:117) and App mounts ChatView only while view === 'chat' (src/renderer/src/App.tsx:216).  
Fix: Keep the unsent composer text per conversation in a small dedicated store, read by key, and stop holding it in component state. Renderer only; no backend change.  
Peers: Open WebUI, LibreChat and Chatbox keep an unsent draft per conversation and restore it on return (per-chat input state persisted locally).  
Plan: composer-drafts.

**convo-5** (P1, robustness, M): Opening a missing chat leaves a stuck page; conversation actions fail silently.  
Today: selectChat sets view and focusedConversationId, then awaits api.conversations.get with no catch (src/renderer/src/store.ts:1428-1439). On 404 the page shows the greeting with a focused id that has no session; send -> openSession rejects and Composer swallows it (Composer.tsx:74-75).  
Fix: All changes are renderer-side; the backend already returns the right codes (404 from GET/PATCH `/conversations/{id}` for a trashed row, 404 "Conversation not found" from POST `/chat`).  
Peers: Qwen Code fails closed with named errors (session_writer_conflict / session_writer_unavailable) and a 'Try again' affordance instead of a dead view.  
Plan: model-and-action-feedback.

**convo-6** (P1, feature, M): No edit-and-resend or rewind to an earlier message.  
Today: User bubbles are plain text with Copy as the only action (src/renderer/src/components/Message.tsx:123-124, 140-163). The only history mutation is Regenerate on the last reply.  
Fix: Soft rewind: hide a user message and everything after it, hand its text back to the composer, allow one Undo until the next send. Nothing is deleted.  
Peers: Codex: Esc-Esc backtrack picks an earlier prompt, rolls the thread back to before it and puts the prompt back in the composer.  
Plan: edit-and-resend.

**convo-7** (P2, feature, M): No search over what was said in chats (title substring only).  
Today: Sidebar search is a client-side `title.toLowerCase().includes(q)` over the already-loaded list (src/renderer/src/components/Sidebar.tsx:135-147, 240-271). The only FTS tables are chunks_fts and memories_fts (backend/personal_os/db.py:438-443); there is no messages index, no search route and no agent tool that can find an earlier chat.  
Fix: Add full-text search over message bodies, surfaced in the sidebar's existing search box. User-facing search only; the agent recall tool is deferred (see end).  
Peers: Goose: search_chat_history runs SQL over messages and returns snippets grouped by session, also exposed to the agent as a recall tool.  
Plan: parked.

**convo-8** (P2, robustness, M): Chat list is unpaginated, refetched whole after every reply, and never pushed between windows.  
Today: Conversations.list does SELECT * with no LIMIT and filters job/desk rows in Python (backend/personal_os/repos.py:109-115); the only index leads with project_id (db.py:52). The renderer refetches the whole list on init and on every `done` (src/renderer/src/store.ts:1413, 1011) and renders every row (Sidebar.tsx:274-289).  
Fix: Keep the list unpaginated. Fix the four confirmed defects.  
Peers: Open WebUI persists the generated title and pushes it as a chat:title event to every client.  
Plan: parked.

**convo-9** (P2, feature, M): Auto-titles are raw 48-character truncations and never improve.  
Today: _title_from cuts the first user message at 48 characters mid-word (backend/personal_os/app.py:1320-1322) and runs only while title == 'New chat' with no earlier user message (app.py:1363-1366). Canned prompts such as the Home 'Brief me' button (src/renderer/src/components/HomeView.tsx:186-189) produce identical rows.  
Fix: 1.  
Peers: OpenCode: a hidden title agent on a small model, only while the title is still the default, failures ignored.  
Plan: parked.

**convo-10** (P2, feature, M): No fork / duplicate of a conversation from a chosen message.  
Today: The conversation routes are list/create/get/patch/delete only (backend/personal_os/app.py:1037-1081); repos.Conversations has no copy (backend/personal_os/repos.py:99-206). The only way to explore an alternative is Regenerate, which replaces the last reply in place.  
Fix: BACKEND  
Peers: OpenCode: Session.fork clones messages up to a chosen message with new ids, titled 'X (fork #1)', with lineage tracked.  
Plan: parked.

**convo-11** (P2, ui, M): A reply that finishes in the background leaves no lasting mark.  
Today: On `done` the row dot turns green and a 6-second timer resets it to idle whether or not the user looked (src/renderer/src/store.ts:70, 826-832, 1009-1010; ChatPulse.tsx:15-18).  
Fix: Persist "a reply finished after you last looked" on the conversation row, draw it from the list row, and delete the dead in-memory counter.  
Peers: LibreChat: a running-conversation indicator in the sidebar fed by the server.  
Plan: parked.

**convo-12** (P2, bug, M): Context toggles on a new chat do nothing, and the tools map is overwritten wholesale.  
Today: On a draft the drawer shows live Memory / Auto-learn / Tools switches (src/renderer/src/components/ContextDrawer.tsx:146, 195-204, 226), but setChatSettings with no conversation id keeps only effort and fast (src/renderer/src/store.ts:1496-1506) and send applies only those (store.ts:1574-1586), so the first message always goes out with...  
Fix: A. Draft settings (renderer + create route)  
Peers: Open WebUI: a temporary chat toggle set before the first message, which skips saving and the title/tag tasks.  
Plan: parked.

**convo-13** (P2, feature, L): No pin, archive, move-to-project or row menu for chats.  
Today: ConvPatch accepts title, model and settings only (backend/personal_os/app.py:1031-1034; repos.py:143-153); conversations has no pinned or archived column (db.py:43-51, 531). project_id changes only when trash-restore falls back to Personal.  
Fix: Scope: pin, archive, move-to-project and a row menu with inline rename. No multi-select and no export in this change.  
Peers: Open WebUI: chat menu with Rename, Pin, Clone, Move to folder, Archive/Unarchive, Download, Delete, plus an Archived Chats settings page.  
Plan: parked.

**convo-14** (P2, feature, M): No per-conversation export; whole-app export leaks trashed chats and drops tool activity.  
Today: There is no per-chat export route or button; per message there is only Copy (src/renderer/src/components/Message.tsx:162).  
Fix: 1. Shared renderer in backend/personal_os/backups.py  
Peers: LibreChat: export modal with markdown, text, json, csv and png, optional branches and settings metadata, sanitised filename.  
Plan: parked.

**convo-15** (P2, ui, M): Chats cannot be opened or switched from the keyboard; no find in transcript.  
Today: Sidebar and project-page chat rows are divs with role=button and tabIndex=0 but only onClick, so Enter/Space do nothing (src/renderer/src/components/Sidebar.tsx:222, 278; ProjectView.tsx:85).  
Fix: Renderer and menu only; no backend, preload or IPC changes. All four menu actions ride the existing `sendMenu` -> `window.os.onMenu` -> `wireMenu()` path.  
Peers: big-AGI: a full chat shortcut set (new, delete, regenerate last, history back/forward, model dropdown).  
Plan: parked.

**convo-16** (P2, feature, M): No per-chat usage total, and stored message timestamps are never shown.  
Today: usage_log rows carry conversation_id but the report groups by model, kind and project only (backend/personal_os/usage.py:134-164) and the only index is on created_at (backend/personal_os/db.py:116-129).  
Fix: A. Per-chat usage total (backend)  
Peers: pi: /session shows message count, token usage and cost for the session.  
Plan: parked.

#### Composer

**composer-1** (P1, bug, M): Composer draft is one shared useState: lost on any view change, leaks between chats, gone on restart.  
Today: The draft is `useState('')` in src/renderer/src/components/Composer.tsx:22. ChatView mounts Composer unkeyed (ChatView.tsx:117) and App mounts ChatView only while `view === 'chat'` (App.tsx:216).  
Fix: Renderer-only; no backend change.  
Peers: LibreChat (useAutoSave: text and files per conversation), AnythingLLM (usePromptInputStorage: localStorage scoped to workspace+thread), Zed (draft_prompt_store per thread), Open WebUI (draft restored...  
Plan: composer-drafts.

**composer-2** (P1, bug, M): Dropping a file outside the composer box navigates the app window to the file.  
Today: Only the `.composer` div calls preventDefault on dragover/drop (Composer.tsx:86-87). No window-level drop or dragover listener exists in src (grep).  
Fix: Three layers, smallest and most important first.  
Peers: OpenCode (drag-overlay.tsx: a full drag overlay over the prompt area), Goose (useFileDrop hook on the chat surface), Open WebUI and LobeChat (whole chat pane is the drop target with an overlay).  
Plan: composer-drafts.

**composer-3** (P2, feature, M): A mid-reply message can only interrupt: no queue-until-done, no pending tray.  
Today: `send` has two outcomes while a reply is answering: `api.steer` or, if that fails, a new run (store.ts:1540-1553). A steer always cancels the provider read and closes the current segment (backend/personal_os/app.py:1689-1716, 2529-2551).  
Fix: Renderer-only change; no backend edits. Enter keeps steering.  
Peers: Open WebUI (per-chat queue, rows with Send now / Edit / Delete, drained when the last reply is done and no file is uploading), Goose (queue above input, Send now, reorder, paused by Stop), LobeChat...  
Plan: parked.

**composer-4** (P1, bug, M): Steer races: a steer before the first token is stored below its answer, and a steer during context assembly is sent to the model twice.  
Today: (a) When a steer arrives and the segment has no text, reasoning or tools, the existing assistant row is reused (app.py:1692 `if buf or tool_events or rbuf`). That row was inserted at app.py:1410, before the steer's user row (app.py:2546), and history is ordered by created_at (repos.py:191-197).  
Fix: All behaviour changes are in the backend; the renderer already applies the needed events (`applyEvent` in src/renderer/src/store.ts:658-681 merges `user_message` by id, filters on `removed_message`, appends on `assistant_message` and repoints `streaming.messageId`).  
Peers: Chatbox (a steer ends the interrupted segment and continues in a fresh assistant message; one generation per session consumes steers; inFlight flag prevents double consumption), Zed...  
Plan: context-and-history, chat-loop-hardening-2.

**composer-5** (P2, robustness, M): Attachments have no chips or pending state: a send can outrun the upload, and the taint and project target can land on the wrong chat.  
Today: `attach` awaits a sequential upload and only then appends a sentence to the draft (Composer.tsx:57-68, store.ts:2859-2872); Send stays enabled, so Enter during a 15 MB upload sends without the note, which then appears alone in the empty box.  
Fix: Keep attachment state local to the Composer, next to the local draft text. Do not add a store slice.  
Peers: Open WebUI (files carry status uploading/error; sends and queue drain are held while any is uploading; spinner on the thumbnail), Jan (blocks send with a toast while attachments are ingesting),...  
Plan: parked.

**composer-6** (P2, robustness, M): No paste handling and no size bound: pasted screenshots are ignored, huge pastes go straight into the message.  
Today: There is no onPaste handler in Composer.tsx, SmartTextarea.tsx or ChatView.tsx (grep finds onPaste only in McpSettings, GoogleSettings and EmailCard). A pasted image does nothing.  
Fix: Bound the size of one chat message relative to the context window, enforced before anything is persisted, and tell the user at paste time. No paste-to-file conversion and no image input.  
Peers: Open WebUI (largeTextAsFile: a paste over 1000 chars becomes an attached text file; mod+shift+V pastes raw), OpenCode (paste.ts module, image attachments), codex (image paste from clipboard;...  
Plan: parked.

**composer-7** (P1, bug, S): Images and unreadable files upload 'successfully' but the model gets only a placeholder.  
Today: A file with no extractable text is stored with '[File x (image/png, N bytes). No text could be extracted…]' (backend/personal_os/extract_text.py:168-185) through `_store_upload` (app.py:3819-3832).  
Fix: Ship Phase 1 (honest failure) now. Phase 2 (image input) is a separate follow-up ticket; its required corrections are listed at the end and are not part of this effort estimate.  
Peers: OpenCode (image prompt parts sent to the model), Goose and codex (image paste and preview, sent as image input), pi (images.autoResize to 2000x2000 and a blockImages switch), LobeChat (capability...  
Plan: composer-drafts.

**composer-8** (P2, ui, M): Stop has no shortcut, no pending state and leaves no trace.  
Today: `store.stop` is called only from the Stop button (Composer.tsx:105); there is no Esc binding. It awaits the POST and swallows errors (store.ts:1688-1695); the backend's `{ok: false}` for an unbound message id (app.py:3447-3456) is ignored.  
Fix: Renderer  
Peers: Open WebUI (Escape in the input stops generation; registered in the shortcut registry; reused by Send-now), Goose (useEscapeKey; Stop pauses the queue with an explanatory label), pi (aborting returns...  
Plan: stream-settle.

**composer-9** (P2, robustness, M): Ghost-text completion calls the LLM on every typing pause with no opt-out, no cancellation and no IME guard.  
Today: With 8+ chars and the cursor at the end, each 600 ms pause posts to /assist/complete (SmartTextarea.tsx:35-36, 47-60; Composer.tsx:91-97), which runs extractionModel or defaultModel (backend/personal_os/assist.py:49-60; app.py:4122-4127).  
Fix: 1. Setting - `backend/personal_os/llm.py`: add `"inlineCompletion": True` to `DEFAULT_SETTINGS`.  
Peers: Open WebUI (input autocomplete is off by default behind ENABLE_AUTOCOMPLETE_GENERATION, with a max input length), qwen-code (follow-up suggestion has an opt-out and is skipped after cancelled or...  
Plan: parked.

**composer-10** (P1, bug, M): Model, effort, fast and plan-mode changes fail silently, and 'Restore defaults' races two PATCHes.  
Today: `setChatModel` and `setChatSettings` await the PATCH with no try/catch (store.ts:1489-1509) and are called as `void …` from ChatControls.tsx:30-32.  
Fix: 1. Backend: make the settings merge atomic — backend/personal_os/repos.py, `Conversations.update` - In the `settings` branch, open a write transaction before the SELECT: `if not c.in_transaction: c.execute("BEGIN IMMEDIATE")`. - Keep the merge shallow, `{stored, patch}`.  
Peers: OpenCode (each user message records its model, so a switch is visible and durable per message), AnythingLLM (per-thread effort stored and reflected in the control), qwen-code (queued entries capture...  
Plan: model-and-action-feedback.

**composer-11** (P3, feature, M): No way to recall a previous prompt from the keyboard.  
Today: Composer handles only Enter (Composer.tsx:101) and SmartTextarea only Tab and Escape (SmartTextarea.tsx:76-89). User messages render as plain text with no actions (Message.tsx:122-124, 141-164).  
Fix: Renderer-only; no backend, store-shape or API change.  
Peers: OpenCode (Up at the start of an empty box recalls prompts, 100-entry history, in-progress draft restored on Down), gemini-cli (useInputHistoryStore persisted across sessions), Goose (Cmd/Ctrl+Up/Down...  
Plan: parked.

**composer-12** (P1, robustness, M): Model picker is not capability-aware: non-chat models listed, effort offered to every model, no retry, model not sticky.  
Today: `list_models` returns every id from /models (backend/personal_os/llm.py:423-430) and `ModelInfo` is `{id}` only (shared/types.ts:1177-1179), so embedding models appear as chat choices. ModelMenu shows a static six-level EFFORTS list for every model (ModelMenu.tsx:8-15, 174-185).  
Fix: 1. Capture capabilities where `/model/info` is already fetched — backend/personal_os/usage.py - In `Pricing.refresh`, inside the existing loop over `data`, also fill `self._caps[model_name]` from `model_info.mode` and `model_info.supports_reasoning`. - Store a key only when its value is not None.  
Peers: AnythingLLM (ReasoningEffortButton shown only when the current model supports reasoning controls), OpenCode (manage-models dialog to hide models; model resolved from the last message), LobeChat...  
Plan: model-and-action-feedback, llm-retry-transport, stream-settle.

**composer-13** (P3, ui, M): No context or draft-size indicator near the composer.  
Today: The only context meter is `ContextMeterView` inside the context drawer (ContextDrawer.tsx:21-43), which is closed by default and fetched from `api.contextMeter`. The composer renders no counter (Composer.tsx:78-119).  
Fix: Add a small, threshold-gated context gauge to the composer footer of real chats. It shows estimated history + system block + current draft against the configured window.  
Peers: big-AGI (TokenProgressbar: draft + history + reserved response stacked against the model limit, warning and danger colours, tooltip with counts and estimated cost), Chatbox (draft token counts off...  
Plan: parked.

**composer-14** (P2, ui, M): Idle composer is unlabeled, does not hold focus, and the setup notice neither blocks sending nor recognises all keyless endpoints.  
Today: ChatView passes no placeholder (ChatView.tsx:117) and the textarea has no aria-label (SmartTextarea.tsx:97-108), so the idle box is empty and unnamed.  
Fix: Four small changes, all in the normal-chat composer path.  
Peers: Goose (useFocusOnTyping refocuses the input when typing anywhere in the window), Open WebUI (Shift+Esc focuses the input; central shortcut registry with an in-app cheatsheet), LibreChat (shortcuts...  
Plan: parked.

**composer-15** (P2, feature, M): No slash commands or @-references in the composer.  
Today: No slash or mention handling in Composer.tsx, SmartTextarea.tsx or ChatView.tsx (grep). The nearest equivalent is dragging a document onto a canvas chat widget, which inserts 'Read document <id> … with read_document' (canvas/widgets/chat.tsx:163-165).  
Fix: Renderer-only change; no backend route or schema changes.  
Peers: Open WebUI (/ for saved prompts, # to attach a file or knowledge item, @ to switch model), LibreChat (/ for prompts and skills, @ for model or agent), Goose (slash commands generated from recipes and...  
Plan: parked.

## 5. Implementation plan

14 packages in two waves close 63 of 95 gaps (some gaps are shared between packages); 32 are parked. Each `_chat_stream` region and each shared file has one owning package in a wave, so packages in a wave can be built in parallel. `llm.py` is split: parsing and classification land in wave 1, the retry loop in wave 2. Two standing constraints from the agent-loop work apply: no change to budgets, and no change to `maxToolRounds`.

### Wave 1

**`llm-stream-core`** (L): Provider stream parsing and error classification (llm.py). Closes llm-10, llm-16, loop-4, llm-6, client-11. Depends on none.  
backend/personal_os/llm.py is honest about what the provider sent: <think> blocks become reasoning and never answer text, list-shaped content does not crash a reply, tool-call fragments assemble by slot with unique ids, a stream that dies without finish/[DONE] is flagged incomplete, and every LLMError carries a machine-readable kind (including a parsed context limit for overflow).

**`reply-outcome`** (L): Reply outcome: finish_reason, timeouts, stop and interruption persisted on the message; resume widened. Closes llm-4, loop-4, llm-2, loop-11, render-7, llm-6, client-2, loop-1, loop-6, llm-11. Depends on none.  
Every way a reply ends (length, content filter, incomplete stream, empty round, time budget, user Stop, backend shutdown, provider error kind) is classified after the model round, persisted on the messages row as `outcome` / `error_kind`, carried on the final `done`, and a stopped / budget-cut / errored / interrupted reply is resumable through one backend query. Tool-call ids are unique across rounds.

**`stop-and-delete`** (M): Stop interrupts a running tool; delete stops the run; approval rows cannot cross runs. Closes loop-2, convo-1, loop-12, client-9, loop-1. Depends on none.  
Stop takes effect inside the tool phase (a running call is raced against Stop with a short grace for writes, the rest of the round is skipped), deleting a chat or project stops its live run and keeps auto-learn off a trashed chat, single-message delete is scoped and refuses mid-reply, and the approval wait never adopts a row it did not open.

**`regenerate-variants`** (L): Regenerate keeps the old answer: superseded rows, variants, restore on failure, retry row. Closes render-2, convo-2, loop-9, client-10, render-6. Depends on none.  
Regenerate never destroys the previous answer: it is soft-superseded, comes back automatically when the replacement produced nothing (provider error, Stop before the first token, setup failure, shutdown), and stays reachable through a prev/next switcher. A trailing user message with no reply shows a Retry row.

**`attach-live-run`** (L): Attach to a live run from any window and replay the in-flight message. Closes client-1, client-3, render-9, convo-3, client-2. Depends on none.  
Opening a chat whose reply is already running (reload, pop-out, another window, inbox preview, backend restart) shows the whole in-flight message and any pending approval card with no duplicated tail, a terminal `error` event settles the transcript instead of only toasting, and every window learns about runs through one app-topic event.

**`transcript-notices`** (M): Transcript shows how a reply ended with one action per class, Continue/Resume, and a sane auto-scroll. Closes loop-11, llm-2, loop-4, render-7, llm-6, client-11, client-2, loop-6, render-5, render-3. Depends on none.  
Message.tsx and ChatView.tsx render the persisted outcome / error_kind contract (stopped, limits, cut-off, filtered, interrupted) with one action per error class, a Continue/Resume button bound to its own message through the resumable route, and a follow-the-bottom scroller with a jump-to-latest pill that never leaks between chats.

**`render-perf-boundary`** (L): Markdown render performance, stable rich blocks, error boundaries, math fix. Closes render-1, client-6, render-3, client-8, render-15, render-8. Depends on none.  
Streaming no longer remounts code/chart/mermaid/html blocks or re-parses the whole reply per token, tool cards and the context drawer stop re-rendering per delta, one bad markdown block or tool card degrades to a fallback instead of blanking the app, and prices / LaTeX bracket delimiters render correctly. Ships the pure delta buffer that stream-settle wires in.

**`composer-drafts`** (L): Per-chat persistent composer drafts, honest uploads, safe file drops. Closes client-16, composer-1, convo-4, composer-7, composer-2. Depends on none.  
Unsent composer text is keyed per conversation (or per new-chat project / page panel), survives navigation, restart and pop-outs, follows a just-created chat into its new row and comes back on a refused send; uploads that yield no readable text fail honestly; a file dropped outside the composer never navigates the app window.

**`model-and-action-feedback`** (L): Model picker, settings writes and conversation actions stop failing silently; init cannot blank the window. Closes composer-10, composer-12, llm-8, convo-5, client-14. Depends on none.  
Model/effort/fast/plan-mode changes are one serialised PATCH per conversation with the server row as truth and a toast on failure; the picker lists only chat models, hides effort where the model has no reasoning and can retry; opening a missing chat drops it with a toast instead of a stuck page; rename/move/pin toast on failure; init() cannot leave a blank window; unhandled rejections toast once.

### Wave 2

**`llm-retry-transport`** (L): Stream-level retries until the first token, stoppable sends, reasoning_effort / service_tier fallback. Closes llm-3, loop-16, llm-5, llm-8, composer-12. Depends on llm-stream-core, model-and-action-feedback.  
A provider failure before the first token (error frame inside a 200, a drop, an empty stream) is retried under the single llmRetries cap; Stop and the run deadline interrupt the header wait and the compaction summariser; a rejected reasoning_effort / service_tier is dropped, remembered per model and resent once; the user sees one notice when effort was dropped.

**`context-and-history`** (L): Per-model context window, overflow recovery, and earlier tool work visible to the model. Closes loop-3, llm-11, llm-1, loop-14, llm-9, llm-12, composer-4. Depends on llm-stream-core, reply-outcome, regenerate-variants.  
A provider context overflow is recovered once (micro-compact, forced history compaction, same round re-issued) instead of being fatal; the effective window is per model (min of the global setting, the proxy's max_input_tokens and a learned limit) and shared by the compaction gate, the in-run stub trigger and the context meter; each earlier reply's tool calls are replayed to the model as a compact deterministic text record; Stop during context assembly ends the reply as stopped.

**`chat-loop-hardening-2`** (L): Malformed tool calls repaired or refused before the gate; steer races, steer during an approval wait, clean breakers, no teardown gap. Closes loop-5, llm-16, loop-4, llm-4, composer-4, loop-7, loop-11. Depends on reply-outcome, stop-and-delete, llm-stream-core.  
A tool call with broken JSON, an unknown or namespaced name, a signature mismatch or arguments cut at the output limit never reaches the approval gate, a connector or the provider: it is repaired when safe and otherwise answered with a precise tool error. A steer before the first token or during context assembly is ordered and sent once; a steer during an approval wait denies the card with the user's note and folds in with clean breakers; no await sits between the last steer check and the final done; the in-flight tool call is recorded for shutdown salvage.

**`edit-and-resend`** (M): Edit and resend an earlier user message (soft truncation inside the chat run). Closes render-13, convo-6, loop-8. Depends on regenerate-variants, transcript-notices, reply-outcome, stop-and-delete.  
A sent user message can be edited inline and resent; everything from it onward is soft-superseded in the same run that starts the new reply, the stale summary, working plan and pending approvals of hidden turns are invalidated, and nothing is hard-deleted.

**`stream-settle`** (L): Streams that end without done, liveness watchdog, request timeouts, delta coalescing, Stop feedback, background completion notices. Closes render-4, client-2, client-4, client-7, client-13, client-15, composer-8, composer-12. Depends on attach-live-run, render-perf-boundary, transcript-notices, reply-outcome, model-and-action-feedback.  
A run that dies, stalls or closes its stream without a final done settles the open transcript in place (error line, tool rows closed, Continue), the client never waits forever on a silent socket or a hung control request, tokens are coalesced per frame, Stop is acknowledged once with a pending state and an Escape shortcut, and a reply that finishes or needs approval off-screen marks the chat unread and notifies once.

### Parked items

Parked gaps and the reason each was left out of this cycle. Most are P2 features or touch files another package owns. Two P1 gaps are parked as a whole: loop-10 (its row-first reorder would reorder the tape and collide with every `_chat_stream` package; its symptoms are covered piecemeal) and render-10 (ordered parts with reasoning durations needs a `Message.tsx` layout rewrite; wave 3 after the other `Message.tsx` edits settle).

- **loop-10** (P1): The pre-loop phase is outside the stop and error envelope. Reason: deferred as a whole: its `_chat_reply` wrapper, row-first reorder and `context` event would reorder the tape and collide with every package that edits `_chat_stream` this cycle.
- **loop-13** (P2): No message-order or role-alternation repair before a request is sent. Reason: message-order / role-alternation repair before send; llm.py is saturated across both waves.
- **loop-15** (P2): The reply is persisted only at the end, and every streamed token is a synchronous SQLite commit on the event loop. Reason: tape-write coalescing and incremental persistence in runs.py, which attach-live-run owns; a performance refactor for after its tape changes are in.
- **llm-7** (P2): Retries, backoff and compaction are invisible: the chat shows thinking dots for minutes. Reason: retry/compaction visibility needs a new per-message status event cutting across llm-retry-transport (emit), attach-live-run (reduce) and transcript-notices (render); the `status` event name is reserved for a follow-up once those have landed.
- **llm-13** (P2): Usage is lost when a stream fails or is abandoned, and cost is blank outside a LiteLLM proxy. Reason: usage emitted on failed streams and non-proxy pricing; orthogonal to the hardening outcomes and conflicts with the stream_chat attempt loop; fold into a later llm.py cycle.
- **llm-14** (P2): Reasoning and provider tool-call fields are dropped between tool rounds. Reason: reasoning_details replay between rounds; stream_chat's parsing body is rewritten by llm-stream-core and wrapped by llm-retry-transport this cycle; add after.
- **llm-15** (P2): The run budget prices rounds differently from the usage log, and long chats burn the token axis on the prompt. Reason: budget cost parity changes how rounds are priced against the budget; the standing constraint is not to touch budgets in this pass.
- **client-5** (P2): Reconnect is a blind 27.5s that retries non-retryable errors, is not coordinated with the backend supervisor, and shows no progress. Reason: supervisor-coordinated reconnect; stream-settle's idle watchdog and reconnect-from-seq fix the user-visible hang; the gate/sleep plumbing and non-retryable status classes can follow.
- **client-12** (P2): No optimistic user message and no pending state between click and run start. Reason: optimistic pending user message; UI polish that edits send/runStream/ChatView while all three are owned by other packages this cycle.
- **render-10** (P1): Order of text, tool calls and reasoning inside a reply is lost; reasoning has no duration. Reason: feature (ordered text/tool/reasoning parts with reasoning durations): a new backend module plus positional metadata plus a full Message.tsx layout rewrite, while Message.tsx is edited by transcript-notices and edit-and-resend this cycle; it overlaps no other...
- **render-11** (P2): Long tool runs are a wall of rows; collapsed rows hide errors and show raw milliseconds. Reason: tool-row folding and duration formatting; ToolEvents.tsx is owned by render-perf-boundary this cycle.
- **render-12** (P2): Tool output is capped at a 1500-character preview with no way to open the rest. Reason: full tool output viewer; working.py is owned by context-and-history, and the result_id handle it needs now exists on every tool event; the UI follows.
- **render-14** (P2): Half-written markdown flashes as literal markers while streaming; wide tables overflow the column. Reason: streaming markdown repair; MarkdownPreview is being restructured by render-perf-boundary; add the hold-backs after.
- **render-16** (P2): No find-in-conversation. Reason: find-in-conversation feature with an Edit-menu change in main; out of scope.
- **convo-7** (P2): No search over what was said in chats (title substring only). Reason: full-text search (new FTS migration); wave-3 conversation-list package.
- **convo-8** (P2): Chat list is unpaginated, refetched whole after every reply, and never pushed between windows. Reason: list pagination and live row patching between windows; attach-live-run's run_state covers live status; the rest goes to a wave-3 conversation-list package after watchRun has settled.
- **convo-9** (P2): Auto-titles are raw 48-character truncations and never improve. Reason: LLM auto-titles feature; wave-3 conversation-list package.
- **convo-10** (P2): No fork / duplicate of a conversation from a chosen message. Reason: fork from a message; builds naturally on supersede_from from edit-and-resend; wave 3.
- **convo-11** (P2): A reply that finishes in the background leaves no lasting mark. Reason: persisted unread columns; the in-memory unread dot and the one-shot notification in stream-settle (client-13) cover the visible gap; persistence can follow without touching the chat loop.
- **convo-12** (P2): Context toggles on a new chat do nothing, and the tools map is overwritten wholesale. Reason: draft settings bag and create-with-settings; model-and-action-feedback keeps the existing draftEffort/draftFast fields to limit store churn, and its serialised writeConversation removes the lost-update half of the symptom.
- **convo-13** (P2): No pin, archive, move-to-project or row menu for chats. Reason: effort-L feature (pin/archive/move/row menu); no P1 in the conversation-list theme, wave 3.
- **convo-14** (P2): No per-conversation export; whole-app export leaks trashed chats and drops tool activity. Reason: per-conversation export; backups.py only gets the superseded filter this cycle (regenerate-variants).
- **convo-15** (P2): Chats cannot be opened or switched from the keyboard; no find in transcript. Reason: keyboard navigation and find in transcript; overlaps render-16, both deferred.
- **convo-16** (P2): No per-chat usage total, and stored message timestamps are never shown. Reason: per-chat usage total and timestamps; wave 3.
- **composer-3** (P2): A mid-reply message can only interrupt: no queue-until-done, no pending tray. Reason: duplicate of loop-7 section D (follow-up queue); a feature rather than hardening, and Composer.tsx / store.ts are already saturated; loop-7 A-C ship in chat-loop-hardening-2.
- **composer-5** (P2): Attachments have no chips or pending state: a send can outrun the upload, and the taint and project target can land on the wrong chat. Reason: attachment chips and pending state would be a second wave-1 rewrite of Composer.tsx on top of the drafts store; schedule after composer-drafts lands.
- **composer-6** (P2): No paste handling and no size bound: pasted screenshots are ignored, huge pastes go straight into the message. Reason: paste handling and a message size bound touch Composer.tsx, SmartTextarea and the chat route; no slot without pushing composer-drafts past budget, and the backend bound wants the per-model window from context-and-history.
- **composer-9** (P2): Ghost-text completion calls the LLM on every typing pause with no opt-out, no cancellation and no IME guard. Reason: ghost-text opt-out/cancellation is a separate subsystem (assist.py / SmartTextarea), not chat hardening; its own small follow-up.
- **composer-11** (P3): No way to recall a previous prompt from the keyboard. Reason: prompt history recall; a small feature on top of the drafts store, after composer-drafts.
- **composer-13** (P3): No context or draft-size indicator near the composer. Reason: context gauge; not trivial (needs the context meter per keystroke).
- **composer-14** (P2): Idle composer is unlabeled, does not hold focus, and the setup notice neither blocks sending nor recognises all keyless endpoints. Reason: composer labelling, focus and setup gate; unrelated to the hardening themes and would widen composer-drafts.
- **composer-15** (P2): No slash commands or @-references in the composer. Reason: slash commands and @-references; a feature outside the hardening scope.

Wave 3 candidates named in the plan: a conversation-list package (search, pagination, titles, pin and archive, export, fork), an ordered-parts transcript package (render-10), tool-output viewer and streaming-markdown hold-backs on top of the render package, and a later `llm.py` cycle for reasoning replay, usage on failed streams and message-role repair.

## 6. Sources

Code maps are from reading the `chat-hardening` worktree. Peer repositories were read from shallow clones or docs on 2026-10-02; evidence paths in section 3 are relative to each repository root.

- OpenCode: https://github.com/sst/opencode
- Kilo Code: https://github.com/Kilo-Org/kilocode
- DeepSeek-TUI (now Codewhale): https://github.com/Hmbown/DeepSeek-TUI
- DeepSeek Harness: https://github.com/deepseek-ai/deepseek-harness (search results only)
- Codex: https://github.com/openai/codex
- gemini-cli: https://github.com/google-gemini/gemini-cli
- qwen-code: https://github.com/QwenLM/qwen-code
- Goose: https://github.com/block/goose
- Open WebUI: https://github.com/open-webui/open-webui
- Cline: https://github.com/cline/cline
- Roo Code: https://github.com/RooCodeInc/Roo-Code
- LobeChat: https://github.com/lobehub/lobe-chat
- Cherry Studio: https://github.com/CherryHQ/cherry-studio
- LibreChat: https://github.com/danny-avila/LibreChat
- crush: https://github.com/charmbracelet/crush
- pi: https://github.com/badlogic/pi-mono
- aider: https://github.com/Aider-AI/aider
- Zed: https://github.com/zed-industries/zed
- Continue: https://github.com/continuedev/continue
- OpenHands: https://github.com/All-Hands-AI/OpenHands (software-agent-sdk)
- Chatbox: https://github.com/chatboxai/chatbox
- Jan: https://github.com/janhq/jan
- AnythingLLM: https://github.com/Mintplex-Labs/anything-llm
- big-AGI: https://github.com/enricoros/big-AGI
- Open WebUI changelog: https://raw.githubusercontent.com/open-webui/open-webui/main/CHANGELOG.md (v0.11.3, v0.11.4)
- Internal: `docs/research/sota-agentloop.md` (agent loop, runs, approvals); per-gap verification notes and the plan package texts used for section 5 were produced in the same research job.
- URLs for repositories were not all fetched by the survey; where a survey recorded only a local clone, the repository URL above is the project's public home and was not re-checked.
