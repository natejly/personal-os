# Track B — Tool design, extensibility, and how agents reach new capabilities

Research date: **2026-09-29**. Target: Personal OS (Electron + React, Python FastAPI sidecar, SQLite,
LiteLLM → Fireworks, default `kimi-k3`, single local user, 25 flat always-injected tools,
network-less `sandbox-exec` Python sandbox, no MCP, no skills).

Everything below is written against that stack. Where evidence comes from Anthropic's own
server-side features on Claude models, I flag it — those numbers do **not** transfer directly to
kimi-k3 through an OpenAI-compatible endpoint, but the *mechanism* is reimplementable client-side,
and in several cases more easily than for Anthropic's own API customers, because you own the
message loop.

---

## 0. TL;DR — the eight things that matter most here

1. **You are one integration away from the tool-count cliff.** Anthropic's own docs state selection
   accuracy "degrades once you exceed 30–50 available tools"
   ([tool search tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)).
   You have 25. Adding one MCP server (GitHub ≈ 90 tools, Slack ≈ 30) puts you over it immediately.
   Build the tool-search/deferred-loading layer **before** the MCP client, not after.
2. **Consolidate before you scale.** Anthropic's tool-writing guidance is explicit that the first
   move is fewer, higher-level tools, not better routing: "More tools don't always lead to better
   outcomes… a common error is tools that merely wrap existing software functionality."
   Your `todo_list/todo_add/todo_update/todo_delete` and `board_*` families are exactly that shape.
3. **Tool results are your biggest token sink and your 24 000-char truncation is a bug, not a
   feature.** Replace truncation with pagination + `response_format` verbosity + **result handles**.
4. **Code-mode is the single highest-leverage change for data-heavy work** — but it needs a
   controlled hole in your `sandbox-exec` policy (a Unix socket to a host-side tool broker). That
   broker is also where per-tool permission checks and the approval card must live.
5. **Skills (SKILL.md) are cheaper and more valuable than more tools** for a life-OS: ~100 tokens of
   metadata each, body loaded only on trigger. Open WebUI's `view_skill` lazy-loading design is the
   closest existing template for an OpenAI-compatible single-user app.
6. **Procedural memory has the best evidence-to-effort ratio in this whole track**: AWM gives
   +24.6% / +51.1% *relative* success on Mind2Web/WebArena purely from inducing reusable workflows
   from successful runs ([arXiv 2409.07429](https://arxiv.org/abs/2409.07429)). You already store
   every successful chat; you're one extraction pass away from a skill library.
7. **MCP has changed shape substantially (2026-07-28 spec is stateless-first)** and the ecosystem has
   a real, documented attack history. Embed a client, but treat every server as hostile: annotations
   are explicitly untrusted, and your existing `danger` ladder must be applied by *you*, not inherited
   from the server.
8. **Tool-use examples are the cheapest accuracy win for a non-frontier model.** Anthropic measured
   72% → 90% on complex parameter handling just by adding sample calls
   ([advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)). For kimi-k3 this
   is likely worth *more* than it is for Opus.

---

## 1. Tool design that actually works

### 1.1 Anthropic's guidance, distilled

Primary source: [Writing effective tools for agents — with agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
(Anthropic Engineering, Sept 2025).

| Principle | What it says | Applied to Personal OS |
|---|---|---|
| **Consolidate, don't wrap** | `schedule_event` should replace `list_users` + `list_events` + `create_event`; `get_customer_context` replaces three getters. "Fewer, more thoughtful tools outperform many generic ones." | Collapse `todo_list/add/update/delete` into `todos(action, …)` or, better, a workflow tool `todo_apply(changes[])`. Same for `board_*` (4 tools → 1–2). Collapse `gmail_search`+`gmail_read` into `gmail_find(query, include_body=false)`. Realistic target: **25 → ~14 tools** with no capability loss. |
| **Namespacing** | Prefix or suffix by service/resource (`asana_search`, `jira_search`; `asana_projects_search`). Choice of prefix vs suffix has measurable effect. | You already prefix (`graph_`, `board_`, `gmail_`). Keep it and make it strict — it's also what makes regex/BM25 tool search work later (Anthropic's own optimization tip: "use consistent namespacing so one search matches the whole group"). |
| **Return high-signal context** | Return `name`, `image_url`, `file_type` — not `uuid`, `256px_image_url`, `mime_type`. "Resolve cryptic UUIDs to semantic identifiers" to improve precision and reduce hallucination. | Your document/memory/graph tools should return titles, dates and snippets, not row ids. Where an id is needed for a follow-up call, return **both** and label it (`doc_id` + `title`). |
| **`response_format` enum** | A `ResponseFormat` enum of `concise` / `detailed` let the agent pick verbosity; Anthropic's worked example is **206 tokens detailed vs 72 tokens concise** for the same call. | Add `response_format: "concise" | "detailed"` to `document_search`, `memory_search`, `graph_search`, `gmail_search`, `web_search`. Default **concise**. This is a ~1 hour change with an immediate context-budget payoff. |
| **Token efficiency by construction** | "Implement pagination, filtering, range selection, and truncation with sensible defaults." Claude Code "restricts tool responses to 25 000 tokens by default." | You truncate at **24 000 chars** (~6 000 tokens) — but hard truncation destroys the tail of the result silently. Replace with: `limit`/`offset` args, a returned `total`/`has_more`, and an explicit `"[truncated: 412 of 1 890 rows shown; call again with offset=412]"` footer. |
| **Steering language in descriptions** | Guide the agent toward efficient patterns — e.g. "make many small, targeted searches instead of one broad search." | Put this in `document_search`'s description. It directly counteracts the "one giant search then flounder" failure mode you'll see with a non-frontier model. |
| **Error messages are prompts** | Unhelpful: opaque codes / tracebacks. Helpful: specific, actionable guidance **with a corrected example**. "Prompt-engineer error responses to show agents precisely what went wrong and how to fix it." | See §1.3. |
| **Descriptions are the highest-leverage knob** | "Describe as you would to a new team hire — making implicit context explicit." Anthropic attributes Sonnet 3.5's SWE-bench Verified SOTA partly to precise tool-description refinement. Use unambiguous parameter names (`user_id`, not `user`). | For kimi-k3 this matters more than for Claude. Budget a focused pass over all 25 descriptions. |
| **Evals, with agents in the loop** | Build realistic multi-step tasks ("Schedule a meeting with Jane next week… attach notes… reserve a room"), not toy ones. Collect: top-level accuracy, runtime per task/tool, number of tool calls, token consumption, tool errors. Use held-out test sets. Let Claude read the transcripts and propose tool changes. | You already emit `tool_events` + span traces + token/cost accounting (`trace.py`, `usage.py`) — you have the *instrumentation* for this for free. What's missing is a fixture harness that replays ~20 canned tasks against a frozen SQLite snapshot. |

OpenAI's guidance agrees on substance: write descriptions that pass the "intern test," use enums and
object structure "to prevent invalid states," use `strict: true`, and — notably — **"Don't make the
model fill arguments you already know."** Pre-compute values the app has (current project id, today's
date, the user's timezone) and inject them server-side rather than exposing them as parameters
([OpenAI function calling guide](https://developers.openai.com/api/docs/guides/function-calling)).
In Personal OS that means `current_time` probably shouldn't be a tool at all — it should be a line in
the system prompt, saving a whole round-trip that a weaker model will otherwise waste.

### 1.2 Natural-language-ish results vs raw JSON

The evidence points to *both*, layered:

- MCP's tool-result design encodes this directly: a result carries human-readable `content` blocks
  **and** optional `structuredContent` validated against an `outputSchema`; the spec says a tool
  returning structured content "SHOULD also return the serialized JSON in a TextContent block"
  ([MCP tools spec, 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)).
- Anthropic's own advice is to make the *model-facing* surface semantic (names over uuids) while
  keeping ids available for chaining.
- OpenAI: "Return function results as strings where format (JSON, error codes, plain text) is your
  choice… for functions with no return value, return a success/failure indicator."

**Concrete for this app:** standardise a tool-result envelope in `tools.py`:

```
<result tool="document_search" total="47" shown="5" truncated="true">
1. "Q3 planning notes" (doc_id=d_8812, 2026-08-14) — …snippet…
…
[42 more. Refine the query or call with offset=5.]
</result>
```

Prose-with-embedded-ids beats a JSON array of objects for weaker models, costs fewer tokens than
pretty-printed JSON, and keeps the ids the model needs. Keep the raw JSON available in
`tool_events` for the Context panel — the UI can render structure even when the model sees prose.

### 1.3 Error messages that steer recovery

MCP formalises the distinction you should copy:

- **Protocol errors** (unknown tool, malformed request) → JSON-RPC error. "Clients MAY provide
  protocol errors to language models, though these are less likely to result in successful recovery."
- **Tool execution errors** (API failure, validation error, business-logic error) → a *successful*
  result with `isError: true` and actionable text. "Clients SHOULD provide tool execution errors to
  language models to enable self-correction." The spec's example is exemplary:
  `"Invalid departure date: must be in the future. Current date is 08/08/2025."`

Personal OS today likely surfaces Python exceptions. Rules to adopt in `tools.py`:

1. Never let a traceback reach the model. Catch, classify, and rewrite.
2. Every validation error names the offending field, the constraint, the value received, and a
   corrected example call.
3. Every "not found" error lists near-misses: `board_move_card: no card "Q3 rollout" on board
   "Work". Cards on that board: "Q3 roll-out plan", "Q3 hiring". Did you mean card_id=c_441?`
   (This is Anthropic's skill-authoring advice applied to tools: make validators verbose and name
   the available alternatives.)
4. Permission denials are errors the model must be able to reason about, not dead ends:
   `gmail_send: denied by user for this chat. You can draft instead with gmail_draft, or ask the
   user to approve sending.` Right now a deny is a terminal state; making it a *steering* error is a
   ~20-line change with outsized effect on a non-frontier model's ability to finish the turn
   gracefully.
5. Anthropic's skill guidance generalises here too: **"Solve, don't defer"** — where the tool can
   recover itself (create a missing file, coerce a date format), do it and *say so in the result*
   rather than erroring back to the model.

### 1.4 Tool-use examples (highest ROI single change)

Anthropic shipped `input_examples` on tool definitions and measured **72% → 90% accuracy on complex
parameter handling** ([advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)).
The rationale: "JSON schemas are ambiguous" — they can't express that dates are `YYYY-MM-DD`, that
ids look like `USR-XXXXX`, or when to include an optional nested block.

The Claude API has a first-class field for this; OpenAI-compatible endpoints don't. **You can get
95% of the benefit for free** by appending a compact example block to the end of each tool's
`description` string:

```
...existing description...
Examples:
  {"query":"rent increase","project":"apartment","limit":3,"response_format":"concise"}
  {"query":"mortgage rate","after":"2026-01-01"}
```

This costs ~40 tokens per tool and is the change I'd make first, because it targets exactly the
weakness of a non-frontier model: parameter formatting, not tool choice.

### 1.5 How many tools before selection degrades

| Source | Setting | Finding |
|---|---|---|
| [Anthropic tool search docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool) | Claude models, production | "Claude's ability to pick the right tool degrades once you exceed **30–50** available tools." A 5-server MCP setup (GitHub, Slack, Sentry, Grafana, Splunk) ≈ **55k tokens** of definitions before any work. |
| [OpenAI function-calling guide](https://developers.openai.com/api/docs/guides/function-calling) | GPT models | Soft guideline: keep **fewer than 20 functions available at the start of a turn**; hard cap 128. |
| [Anthropic, advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) | Internal MCP evals, large libraries | With tool search on: Opus 4 **49% → 74%**; Opus 4.5 **79.5% → 88.1%**. Context 77k → 8.7k tokens (**−85%**). |
| ["How Many Tools Should an LLM Agent See?"](https://arxiv.org/abs/2605.24660) | BFCL (370 tools), MetaTool (199), ToolBench (3 251) | Adaptive shortlist depth K≈7.4 matches fixed K=50 coverage (90.3% vs 90.8%) at 7× fewer tools; downstream **tool-choice accuracy 93.1% (adaptive) vs 87.1% (fixed K=5)**; up to 11.5 pp gain on medium-difficulty queries from reduced "distractor load." |
| [RAG-MCP](https://arxiv.org/abs/2505.03275) | Large MCP tool pool | Retrieval-first selection: **13.62% → 43.13%** tool-selection accuracy; >50% prompt-token cut. |
| [MCP-Zero](https://arxiv.org/abs/2506.01056) | 308 servers / 2 797 tools (248.1k tokens of defs) | Agent actively *requests* capabilities; **98% token reduction on APIBank** with accuracy retained. |
| [LiveMCPBench](https://arxiv.org/abs/2508.01780) | 70 servers, 527 tools, 95 daily tasks | Claude-Sonnet-4 78.95%; most models 30–50%. **Retrieval errors account for nearly half of all failures.** Weak models averaged ~1 tool per task vs 2.71 for Sonnet-4 — severe tool *under*-utilisation. |

**What this means here.** At 25 tools you are below the cliff but not far below, and your model is
weaker than the ones those thresholds were measured on. Two implications:

- Don't panic-build tool search today; **do** consolidate to ~14 and fix descriptions/examples first.
- The LiveMCPBench "weak models use ~1 tool per task" finding is the most relevant warning for
  kimi-k3: your risk is under-use and premature answering, not over-selection. Mitigate with explicit
  system-prompt tool-category framing (Anthropic's own optimization tip: "You can search for tools to
  interact with Slack, GitHub, and Jira") and with skills that name the tools to use for a task.

---

## 2. Progressive disclosure and tool scaling

### 2.1 The mechanism, precisely

Anthropic's server-side **tool search tool** works like this
([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)):

1. All tool definitions are still *sent* on every request; `defer_loading: true` controls only what
   enters the **context window**.
2. Deferred tools are excluded from the system-prompt prefix. When discovered, the API appends a
   `tool_reference` block **inline in the conversation** and expands it — "the prefix is untouched,
   so prompt caching is preserved." This is the key architectural trick.
3. Two variants: `tool_search_tool_regex_20251119` (Claude writes Python `re.search()` patterns,
   ≤200 chars) and `..._bm25_...` (natural language, ≤500 chars). Both search **names, descriptions,
   argument names, and argument descriptions**.
4. Default 5 results, `limit` up to 10 000; up to 10 000 deferred tools.
5. "Keep your 3–5 most frequently used tools non-deferred." At least one tool must be non-deferred.
6. **You can implement your own**: return `tool_reference` blocks from a custom tool (they document
   an embeddings-based recipe). Use tool search when ≥10 tools, >10k tokens of definitions, or when
   aggregating MCP servers.

### 2.2 The client-side version for an OpenAI-compatible loop

You control `_chat_stream()`, so you can do something Anthropic's API users can't: **mutate the
`tools` array between rounds.** Design:

- Registry in `tools.py` gains `tier: "core" | "deferred"` and a `keywords` field.
- **Core tier (~6 tools, always injected):** `document_search`, `memory_search`, `todos`,
  `current_time` (or drop it), `run_python`, `find_tools`.
- `find_tools(query, limit=5)` runs FTS5 BM25 over (name, description, arg names, arg descriptions)
  — you already have FTS5 in SQLite, so this is a virtual table and ~60 lines.
- Results are appended to a per-run `active_tools` set. On the next round, the request's `tools`
  array = core tools **in a fixed order**, then discovered tools appended. Keeping the core prefix
  byte-identical preserves Fireworks/LiteLLM automatic prefix caching for the definitions block.
  (MCP 2026-07-28 added a matching minor requirement: servers SHOULD return `tools/list` in
  deterministic order "to improve LLM prompt cache hit rates.")
- Persist `active_tools` on the chat so a follow-up turn doesn't re-search.
- Surface it in the Context panel: "3 tools loaded on demand: `gmail_find`, `gcal_create`, …"

**Caveat to flag:** Anthropic's 49%→74% number is for Claude models with server-side expansion. Two
risks for kimi-k3: (a) a weaker model may not *think* to call `find_tools`; (b) it may search badly.
Mitigations: (a) a one-line system-prompt manifest of tool *categories* (not schemas) — ~150 tokens
for 25 tools; (b) prefer BM25/natural-language over regex; (c) consider **eager pre-selection**
instead of model-driven search — run BM25 over the user's message before round 1 and inject the top-K
tools plus the core set. That is RAG-MCP's design (13.62% → 43.13%) and requires zero cooperation
from the model. Given kimi-k3, I'd ship **eager pre-selection first** and `find_tools` as the escape
hatch.

### 2.3 Grouping, toolsets, namespacing

- **Toolsets** are the coarse control: GitHub's official MCP server ships `--toolsets
  repos,issues,pull_requests,…` and `GITHUB_DYNAMIC_TOOLSETS=1` for run-time enablement
  ([github/github-mcp-server](https://github.com/github/github-mcp-server)). "Enabling only the
  toolsets you need can help the LLM with tool choice and reduce the context size."
- **LiteLLM already does server-side MCP namespacing and filtering** for you: it "namespaces multiple
  MCP servers by prefixing each tool name with its MCP server name," supports `litellm_settings.mcp_aliases`,
  per-key/team/org allow-lists, and per-server header forwarding (`x-mcp-{server_alias}-{header}`)
  ([LiteLLM MCP docs](https://docs.litellm.ai/docs/mcp)). Since you already run a LiteLLM proxy, this
  is a genuine build-vs-buy decision for the *transport* half of MCP (see §4.6).
- **Name collisions are guaranteed** once you aggregate. MCP's spec says tool-name uniqueness is
  scoped to a single server, aggregating clients "SHOULD implement a disambiguation strategy such as
  prefixing," and — importantly — "the server `name` from `serverInfo` is not guaranteed to be unique
  across servers and SHOULD NOT be relied upon for disambiguation." Use *your own* stable connection
  id as the prefix, not the server's self-reported name.

### 2.4 The token cost of always-injected schemas — do the arithmetic for your app

Rough: a well-written tool definition is 80–200 tokens. 25 tools ≈ **2–5k tokens on every single
request**, every round, up to 8 rounds. At 8 rounds that's 16–40k tokens of pure schema per user
turn before any content. Add one MCP server and it triples. Two cheap wins before any architecture
change:

1. Strip JSON-Schema noise: drop `title`, redundant `default`s, verbose `description`s on obvious
   fields.
2. Don't inject tools that are **off** for this chat/project. Your permission resolution already
   computes on/ask/off per tool — tools resolved to **off** should not appear in the `tools` array at
   all. If any are currently injected-and-refused, that's free tokens today.

---

## 3. Code execution as the tool interface

### 3.1 The argument and the numbers

[Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)
(Anthropic, Nov 2025) makes two claims:

1. **Definition overhead.** Present tools as a *filesystem of typed modules* the agent imports,
   rather than schemas in context:
   ```
   servers/
     google-drive/  getDocument.ts  index.ts
     salesforce/    updateRecord.ts index.ts
   ```
   The agent explores the filesystem or calls `search_tools` with a detail-level parameter, loading
   "only the tools it needs."
2. **Intermediate-result overhead.** Chaining tools passes every result through the model. Their
   worked example — a meeting transcript moved Drive → Salesforce — flows the transcript through
   context twice, ~50 000 tokens for a 2-hour recording. Rewritten as code:
   **150 000 → 2 000 tokens, "a time and cost saving of 98.7%."**

Supporting mechanisms in the same post: filter/aggregate in the sandbox before returning
(`allRows.filter(r => r.Status === 'pending')`); real control flow (loops, conditionals, try/catch)
evaluated without a model round-trip; intermediate results stay in the execution environment by
default; optional **PII tokenisation** (`{email: '[EMAIL_1]'}`) so real values never enter context;
state persisted to files for resumable workflows; and successful code saved as reusable **Skills**.

Independent and adjacent evidence:

| Source | Number |
|---|---|
| [CodeAct (ICML 2024)](https://arxiv.org/abs/2402.01030) | Code actions vs JSON/text actions: **up to +20.7% absolute success rate**, **2.1 fewer turns** on M³ToolEval (gpt-4-1106-preview); up to 20% higher success and 30% fewer actions across **17 LLMs**. Agents self-debug from tracebacks and use existing packages (pandas, sklearn) unprompted. |
| [smolagents](https://huggingface.co/blog/smolagents) | Code-writing agents take **~30% fewer steps** (hence ~30% fewer LLM calls) and score higher on hard benchmarks. Core agent logic ≈1 000 LOC. |
| [Anthropic PTC](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling) | Average **43 588 → 27 297 tokens (−37%)** on complex research; eliminates 19+ inference passes on a 20-tool workflow; GAIA **46.5% → 51.2%**; internal knowledge retrieval **25.6% → 28.5%**; on BrowseComp/DeepSearchQA, **+11% average with 24% fewer input tokens**. |
| [OpenAI PTC](https://developers.openai.com/api/docs/guides/tools-programmatic-tool-calling) | Deliberately unquantified: "can reduce the amount of intermediate tool output added to model context, but the effect depends on the task… Start with direct tool calling as a baseline, then compare." |
| [Simon Willison](https://simonwillison.net/2025/Nov/4/code-execution-with-mcp/) | Endorses the approach; principal complaint is that Anthropic "outline the proposal in some detail but provide no code to execute on it." |

**Honest read:** the 98.7% figure is a single, favourable, self-reported example. The robust,
replicated findings are CodeAct's +20%/−30% steps and Anthropic's PTC −37% tokens. Treat 30–40%
token reduction and ~20% step reduction on *multi-call data-heavy* tasks as the realistic expectation;
treat the 98.7% as the shape of the best case.

### 3.2 What this requires of your sandbox — the hard part

Today `run_python` is `python -I` under macOS `sandbox-exec`, **no network**, writes confined to a
throwaway dir, CPU/mem/wall limits. That's a good sandbox and the wrong shape for code-mode: code-mode
requires the sandboxed code to *call back out* to your tools.

Design that preserves your security posture:

```
model → run_agent_code(code)
          ↓
  sandbox-exec python -I  (no network, throwaway cwd)
          ↑ ↓  AF_UNIX socket, one allowed path in the sandbox profile
     tool broker (FastAPI process)
          ↓
   existing tool registry + permission resolution + approval Future
```

- Generate a **stub module tree** into the throwaway dir before each run, one file per allowed tool:
  `tools/documents/search.py` etc., each a thin function with a real docstring (name, args, return
  shape) that RPCs over the socket. This *is* the "filesystem of typed modules" pattern, in Python.
  The agent reads `tools/__init__.py` (a table of contents) and `help()`s its way down —
  progressive disclosure for free, with zero schema tokens.
- The `sandbox-exec` profile gains exactly one `(allow network-outbound (literal (path "/tmp/pos-broker.sock")))`
  style rule. No `(allow network*)`. This is a deliberate, auditable, single hole.
- **Every call arriving at the broker goes through the same permission resolution and the same
  approval card as a direct tool call.** The socket is not a bypass. Crucially: an `ask`-tier tool
  called from inside a loop must not spam the user — batch the approvals ("this script wants to send
  3 emails: …") or require the script to declare intended external calls up front.
- Per-run quotas on the broker: max calls, max bytes returned, wall clock. A runaway `for` loop
  calling `gmail_send` is a new failure mode you don't have today.
- `allowed_callers` is instructive as a *design* idea and a warning: Anthropic's docs say it
  "controls how the tool is presented to Claude and is validated against `tool_choice`, but it is not
  a hard API-level block… **Do not rely on `allowed_callers` as a security boundary**." Your broker
  must enforce, not hint.
- Keep `run_python` (pure compute, no broker) as a separate, *safer* tool from `run_agent_code`
  (broker access). Different `danger` levels.

### 3.3 When code-mode wins and when it doesn't

Anthropic's own "best for" list: aggregating large datasets, 3+ dependent calls, filtering/transforming
results, parallel operations. Their "avoid" list (OpenAI's, actually, and it's the better one):
when you need **semantic evaluation of each result, approval gates, citations, or native artifact
preservation**.

For Personal OS that maps cleanly:
- **Code-mode:** "summarise every unread email from this week by sender," "which of my 40 todos
  reference people in my graph," "build a table of last month's calendar events by category."
- **Direct calls:** anything with an approval card, anything where the user needs to see the
  intermediate result, anything citation-bearing (your document tools feed a Context panel).

Also note the hard limits Anthropic and OpenAI both hit: OpenAI's PTC runs in a V8 isolate with **no
network, no filesystem, no persistent state between executions**; Anthropic's PTC cannot call
MCP-connector tools, computer/browser toolsets, `strict:true` tools, or tools with recursive `$ref`
schemas. Expect similar carve-outs.

---

## 4. MCP in depth

### 4.1 The spec moved — 2026-07-28 is stateless-first

[Changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog). This is a *breaking*
revision and it substantially simplifies embedding a client:

**Major:**
1. **No protocol-level sessions.** `Mcp-Session-Id` removed from Streamable HTTP. List endpoints no
   longer vary per connection. Cross-call state now uses **explicit server-minted handles passed as
   ordinary tool arguments**.
2. **No `initialize` handshake.** Every request carries protocol version and client capabilities in
   `_meta` (`io.modelcontextprotocol/protocolVersion`, `.../clientCapabilities`, `.../clientInfo`).
3. **`server/discover`** — servers MUST implement it; clients MAY call it for up-front version
   selection or as a stdio compatibility probe.
4. **`subscriptions/listen`** replaces the HTTP GET endpoint and `resources/subscribe`. Clients opt
   into `toolsListChanged` / `promptsListChanged` / `resourcesListChanged` / `resourceSubscriptions`.
   Request-scoped notifications (`notifications/progress`, `notifications/message`) still flow on the
   originating request's response stream.
5. `ping`, `logging/setLevel`, `notifications/roots/list_changed` removed; log level is per-request
   via `_meta`.
6. **Tasks** moved to an official extension (`io.modelcontextprotocol/tasks`), now poll-based
   (`tasks/get`, `tasks/update`).
7. **MRTR replaces all server-initiated requests** (see §4.4).
8. Every result carries `resultType`: `"complete"` or `"input_required"`.
9. **SSE resumability removed** — a broken stream loses the in-flight request; clients MUST re-issue
   with a new request id.

**Minor, but load-bearing for you:** `CacheableResult` adds required `ttlMs` + `cacheScope` on list
results (so you can cache tool lists instead of polling); deterministic tool ordering SHOULD be
returned "to improve LLM prompt cache hit rates"; OpenTelemetry trace-context conventions documented
for `_meta` (`traceparent`/`tracestate`/`baggage`) — which plugs straight into your `trace.py` spans.

**Deprecated (12-month minimum window under the new feature-lifecycle policy):** **Roots, Sampling and
Logging**. Suggested migrations: pass directories/files as tool parameters or resource URIs instead of
Roots; **integrate directly with LLM provider APIs instead of Sampling**; log to stderr or OTel.
HTTP+SSE transport reclassified Deprecated. OAuth **Dynamic Client Registration deprecated** in favour
of Client ID Metadata Documents.

> **Design consequence for Personal OS:** do **not** build Sampling or Roots support. A year ago the
> advice would have been "implement sampling so servers can borrow your model" — that is now a
> deprecated path. Skip it entirely. Do implement **elicitation**, because that's where your existing
> approval-card UI already fits.

### 4.2 Transports: stdio vs Streamable HTTP

- **stdio** is what a single-local-user desktop app wants for ~90% of servers: subprocess, no auth,
  no network exposure, works offline. This is also what `.mcpb` bundles standardise on.
- **Streamable HTTP** for remote/SaaS servers; needs OAuth 2.1 + PKCE. Note the 2026-07-28 changes:
  standard `Mcp-Method` / `Mcp-Name` headers required on POST; no session header; no stream
  resumability.
- HTTP+SSE: deprecated, don't implement.

### 4.3 Tool annotations — useful for UX, worthless for security

`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`. The spec is unambiguous:
**"clients MUST consider tool annotations to be untrusted unless they come from trusted servers."**
The MCP blog's follow-up ([Tool annotations as risk vocabulary](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/))
is blunter: "An untrusted server can claim `readOnlyHint: true` and delete your files anyway… If you
need a guarantee that a tool can't exfiltrate data, that's a job for network controls or sandboxing,
not a boolean hint." Defaults are conservative — an unmarked tool is assumed destructive,
non-idempotent, and open-world.

**Mapping into your `danger` ladder.** Annotations should *propose*, never *decide*:

| Annotation combination | Proposed `danger` | User can lower it? |
|---|---|---|
| `readOnlyHint: true`, `openWorldHint: false` | `safe` → but start at `writes` | yes, explicitly, per server |
| `readOnlyHint: true`, `openWorldHint: true` | `network` | yes |
| absent / `destructiveHint: true` | `external` (⇒ **ask**) | yes |
| anything from a server not marked trusted | `external` | only after the user marks the server trusted |

And record the annotation values at connect time so you can detect a **rug pull** (see §4.7) when they
change.

### 4.4 Elicitation and MRTR — this is your approval card, upgraded

[Elicitation spec](https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation) +
[MRTR pattern](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr).

- A server that needs more input returns `InputRequiredResult` (`resultType: "input_required"`) with
  an `inputRequests` map and an opaque `requestState` blob. The client gathers input, then **retries
  the original request with a new JSON-RPC id**, echoing `requestState` verbatim.
- **Form mode:** `requestedSchema` restricted to *flat objects of primitives* — string (with
  `email`/`uri`/`date`/`date-time` formats, min/max length), number/integer (min/max), boolean, and
  enums (single/multi, with optional titles via `oneOf`/`anyOf` `const`+`title`). No nesting, no
  arrays of objects. Responses are `accept` / `decline` / `cancel`.
- **Servers MUST NOT request passwords/API keys/tokens/payment credentials via form mode** — those
  must use **URL mode**, where the client shows the full URL, highlights the domain, must not
  pre-fetch it, and must open it somewhere the client/LLM cannot inspect content or input.
- Clients MUST make it clear which server is asking and provide clear decline/cancel.

**Why this is great news for Personal OS:** the restricted flat-primitive schema is *exactly* an
auto-generatable form. Your approval card becomes a general-purpose "server needs something from you"
card with three outcomes that already match your `allow / deny` vocabulary (`accept` / `decline` /
`cancel` ≈ allow / deny / timeout). And because MRTR is retry-based rather than a nested
server→client request, it composes with your SSE loop without a second channel: you get a result,
you pause the loop, you show the card, you re-issue the call.

The `cancel` action also gives you a cleaner story than your current 600 s auto-deny: `cancel` is
"dismissed without an explicit choice," which is semantically what a timeout is.

### 4.5 Resources vs tools vs prompts

Three control planes, and the distinction is the design tool you're missing:

- **Tools = model-controlled.** The LLM decides.
- **Resources = application-controlled.** *You* decide what to inject. `resources/read`, addressable
  by URI, cacheable (`ttlMs`, `cacheScope`).
- **Prompts = user-controlled.** Surfaced as slash commands; no model reasoning about which to pick.

For Personal OS: MCP **prompts** should land in your UI as `/`-commands in the composer (§7), MCP
**resources** should feed your Context panel and `context.py` assembly (they're the natural home for
"attach this server's schema/docs to the chat"), and only **tools** should go into the `tools` array.
Most MCP client implementations only wire up tools and leave the other two on the table; doing all
three is a differentiator and costs little.

### 4.6 Embedding an MCP client in FastAPI — practical guidance

**The SDK surface.** `ClientSessionGroup` aggregates multiple servers
([docs](https://py.sdk.modelcontextprotocol.io/client/session-groups/)):

```python
from mcp import ClientSessionGroup, StdioServerParameters

def by_server(name: str, server_info) -> str:
    return f"{server_info.name}.{name}"

async with ClientSessionGroup(component_name_hook=by_server) as group:
    await group.connect_to_server(StdioServerParameters(command="uv", args=[...]))
    result = await group.call_tool("library.search", {"query": "..."})
```

Documented behaviours and caveats:
- `group.tools` / `.resources` / `.prompts` are merged dicts; `call_tool` routes to the owning server.
- `component_name_hook` runs on **every** name, not just collisions; it changes *local* dict keys
  only — wire names are unchanged.
- Name collisions raise `MCPError`; a group without a hook *will* eventually collide.
- `connect_with_session()` adds an externally-created session; **the group never closes sessions it
  didn't open.**
- `ClientSessionGroup` uses only the classic handshake, not the faster `server/discover` probe.

**The trap you will hit: anyio cancel scopes.** The MCP Python SDK's client transports yield inside
`anyio.create_task_group()` cancel scopes. Tearing a session down from a different task than created
it raises `RuntimeError: Attempted to exit cancel scope in a different task`. This is a well-known,
widely-reported failure across consumers — see
[python-sdk#521](https://github.com/modelcontextprotocol/python-sdk/issues/521),
[langchain-mcp-adapters#466](https://github.com/langchain-ai/langchain-mcp-adapters/issues/466),
[pydantic-ai#8548](https://github.com/pydantic/pydantic-ai/issues/8548).

**Architecture that avoids it:** run all MCP sessions in **one owning task** created in the FastAPI
`lifespan`, and talk to it from request handlers through an `asyncio.Queue`:

```
lifespan startup
  └─ asyncio.create_task(mcp_supervisor())        # owns the task group forever
        ├─ ClientSessionGroup() entered here, exited here
        ├─ reads (call_id, server, tool, args, response_future) from an inbox queue
        └─ per-server reconnect/backoff, tool-list refresh on listChanged
request handler / _chat_stream()
  └─ puts a request on the inbox, awaits its future (this is exactly your _approvals pattern)
```

This also gives you, for free: a single place to enforce per-call timeouts, a single place to apply
permission resolution, a single place to hang tracing spans, and survivability of a server crash
without killing the HTTP request.

**Other practicalities:**
- Store server configs in SQLite (`mcp_servers`: id, name, transport, command/url, env, enabled,
  trusted, created_at) plus `mcp_tools` (server_id, name, description, annotations_json,
  schema_hash, first_seen, last_seen, approved_schema_hash). The `schema_hash` column is your
  rug-pull detector.
- Cache `tools/list` using the new `ttlMs`; refresh on `toolsListChanged` via `subscriptions/listen`.
- **Prefix everything** with your own connection id (`mcp_<connid>_<tool>`), because the spec warns
  `serverInfo.name` is not unique.
- OAuth for remote servers: OAuth 2.1 + PKCE; the 2026-07-28 spec deprecates Dynamic Client
  Registration in favour of **Client ID Metadata Documents**, requires `application_type` on DCR when
  you do use it, requires validating RFC 9207 `iss` in authorization responses, and requires
  credentials be keyed by issuer and **never reused across authorization servers**. For a
  single-local-user desktop app: prefer stdio servers and defer remote OAuth until you actually need
  a SaaS-only server.
- **Build-vs-buy:** LiteLLM can act as the MCP gateway (namespacing, alias config, allow-lists,
  per-server auth headers, `/mcp-rest/tools/list` and `/mcp-rest/tools/call`, and
  `litellm.experimental_mcp_client.load_mcp_tools(format="openai")` /
  `call_openai_tool()`). Since you already run the proxy, this is a legitimate shortcut for remote
  HTTP servers. It is **not** right for local stdio servers (the proxy would have to spawn processes
  on the user's machine) and it puts tool execution outside your permission UI. Recommendation: own
  the stdio path in-process; optionally let LiteLLM front remote servers later.

### 4.7 MCP security: the incidents are real, treat every server as hostile

| Incident | What happened |
|---|---|
| **postmark-mcp backdoor** (Sept 2025) | First confirmed malicious MCP server in the wild. v1.0.16 added one line appending a hidden **BCC** to every email the agent sent. ~1 500 active weekly installs leaking mail daily. ([Checkmarx summary](https://checkmarx.com/learn/mcp-security-risks-real-world-incidents-and-security-controls/), [UpGuard](https://www.upguard.com/blog/mcp-security-incidents)) |
| **Asana MCP cross-tenant exposure** (June 2025) | Project/task data from one org became visible to others. ([Nudge Security](https://www.nudgesecurity.com/post/asana-mcp-server-data-exposure-incident)) |
| **MCP Inspector unauthenticated RCE** | In Anthropic's own developer tool. |
| **npm supply chain** (Sept 2025) | 18 packages including `debug`/`chalk`/`ansi-styles` — indirect deps of the official MCP TypeScript SDK. |
| **Tool poisoning + rug pulls** ([Invariant Labs](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks)) | Malicious instructions hidden **in tool descriptions** — visible to the LLM, not normally shown to the user. Demonstrated exfiltration of a full WhatsApp history via a benign-looking tool on a *different* server. "Rug pull": server changes a tool's description after the user approved it. |
| **Sandworm_Mode** (Feb 2026) | npm typosquatting targeting Claude Code / Cursor / Windsurf, installing rogue MCP servers. |

And the framing that should drive your UI: Simon Willison's
[lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/) — **private data +
untrusted content + external communication**. "If your agent combines these three features, an
attacker can easily trick it into accessing your private data and sending it to that attacker."
Guardrails that catch "95% of attacks" are "very much a failing grade."

**Personal OS is a lethal-trifecta machine by design**: it holds memories/documents (private data),
reads email and web pages (untrusted content), and can send email / write to Google (external
communication). Concrete defences that fit your architecture:

1. **Show the tool description in the approval card**, not just the name and args. Tool poisoning
   works precisely because descriptions are invisible to users.
2. **Pin schemas.** Hash (name + description + input schema + annotations) at approval time. If it
   changes, revoke `always_*` grants for that tool and re-prompt: "this tool's description changed
   since you approved it."
3. **Trifecta detection per chat.** You already track `tool_events`. Compute, per turn: has the
   context ingested untrusted content (web_search/fetch_url/gmail_read/MCP `openWorldHint` tool)?
   If yes, escalate every `external`-tier tool to **ask** for the rest of that chat, regardless of
   `always_chat`. This is cheap, deterministic, and is the only mitigation the literature actually
   endorses.
4. **Never let `always_global` be grantable for a tool from a non-trusted MCP server.** Cap MCP tools
   at `always_chat`.
5. Log tool usage for audit (spec: clients SHOULD); you have `trace.py` — extend spans to MCP calls
   with the OTel `_meta` conventions the spec now documents.
6. Consider running a static scan of server configs/descriptions at install time
   ([mcp-scan](https://invariantlabs.ai/blog/introducing-mcp-scan)).

### 4.8 Registry and distribution

- The **official MCP Registry** launched in preview **Sept 8, 2025** — "an app store for MCP servers,"
  the authoritative index of publicly available servers. This is your server-browser data source;
  don't hand-maintain a list.
- **`.mcpb` (MCP Bundle)** is a zip of a stdio MCP server + `manifest.json`, installed in one click
  ([Claude docs](https://claude.com/docs/connectors/building/mcpb),
  [spec repo](https://github.com/modelcontextprotocol/mcpb)). Characteristics: runs locally, stdio,
  bundles all dependencies, works offline, **no OAuth required**. Critically, `manifest.json` has a
  **`user_config` section from which the host auto-generates a settings UI**, including
  sensitive-data handling. Anthropic has deprecated `.mcpb` *directory listings* in favour of
  plugins, but the format itself is exactly right for a local-first Electron app: adopt the manifest
  schema for your own "install a server" flow rather than inventing one, and you get config-UI
  generation and a compatible ecosystem for free.

---

## 5. Skills / procedural memory

### 5.1 What a Skill is, exactly

[Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) +
[authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices).

A skill is a **directory** with a `SKILL.md`: YAML frontmatter (`name` ≤64 chars, lowercase/digits/
hyphens; `description` ≤1024 chars, third person, must say *what* and *when*) plus a Markdown body,
optional `reference/*.md`, optional `scripts/*.py`.

**Three-level progressive disclosure, with stated token costs:**

| Level | Loaded | Cost | Content |
|---|---|---|---|
| 1 Metadata | always, at startup | **~100 tokens per skill** | `name` + `description` |
| 2 Instructions | when triggered | **under 5k tokens** | SKILL.md body |
| 3 Resources | as needed | **none until accessed** | bundled files; scripts run via bash, only *output* enters context |

That's the whole value proposition: "you can install many Skills without context penalty." A hundred
skills ≈ 10k tokens of always-on metadata; a hundred *tools* would be 10–20k tokens **and** wreck
selection accuracy.

**Authoring rules worth copying verbatim into your own skill format:**
- Body under **500 lines**; split beyond that.
- **References one level deep from SKILL.md.** Claude partially-reads nested references (`head -100`),
  producing incomplete information. Reference files >100 lines get a table of contents at the top.
- **Degrees of freedom matched to task fragility**: high freedom (prose) when many approaches work;
  low freedom (a specific script, "do not modify the command") for fragile sequences.
- **Feedback loops**: "run validator → fix → repeat" and the **plan-validate-execute** pattern
  (write a `changes.json`, validate it with a script, *then* apply). Explicitly recommended for batch
  operations and destructive changes.
- **Workflow checklists** the agent copies into its response and ticks off.
- **Prefer shipped scripts over generated code**: more reliable, fewer tokens, consistent.
- Test with the weakest model you'll run it on. "What works perfectly for Opus might need more detail
  for Haiku." — For you, read: *write for kimi-k3, not for what you'd write for Claude.*
- Anti-patterns: vague names (`helper`, `utils`), offering many options instead of one default +
  escape hatch, time-sensitive statements, inconsistent terminology, Windows paths.
- **Evaluation-driven development**: build ≥3 evals *before* writing the skill; measure the baseline
  without it.
- Skills are a security surface: "a malicious Skill can direct Claude to invoke tools or execute code
  in ways that don't match the Skill's stated purpose… Treat like installing software."

### 5.2 Skill vs tool vs prompt

- **Prompt / system instructions** — conversation-level, one-off, you repeat it each time.
- **Skill** — *procedural knowledge*: how to do a recurring multi-step thing with the tools you
  already have. Loaded on demand. No new capability, new competence.
- **Tool** — a *new capability* the model cannot otherwise reach (I/O, side effects).
- MCP vs Skills, per Anthropic: skills "complement MCP servers by teaching agents more complex
  workflows that involve external tools and software." MCP gives reach; skills give procedure.

**For Personal OS, almost everything on the life-OS roadmap is a skill, not a tool:** morning brief,
daily recap, email triage, pre-meeting brief, weekly review, time blocking. Each is "call these 4
existing tools in this order, apply these rules, format like this." Building them as *tools* would
add 6 more schemas to a context already near the cliff; building them as *skills* costs ~600 tokens
of metadata total and is editable by the user in a text box.

### 5.3 The nearest implementable template: Open WebUI Skills

[Open WebUI skills docs](https://docs.openwebui.com/features/workspace/skills/) — same constraints as
you (OSS, OpenAI-compatible backends, single/multi user, no VM):

- Skills are markdown with YAML frontmatter (`name`, `description`, `version`, optional `platforms`).
- **`$` mention** in the composer injects a skill's full content into the current message.
- **Per-chat toggle** from an Integrations menu; persists for that chat, sent every message.
- **Model binding**: skills attached to a model are always available.
- **Lazy loading**: for model-attached skills under native function calling, only name + description
  go into the system prompt, and the model gets a **`view_skill` builtin tool** to load the full body
  on demand. Under legacy function calling everything is injected in full.

That `view_skill` tool is Anthropic's Level-1/Level-2 progressive disclosure reimplemented with
nothing but an OpenAI-compatible tool call — which is exactly what you have. **This is the design to
copy.** Personal OS version:

- `skills` table in SQLite: id, name, description, body, scope (global / project / chat), enabled,
  source (`user` | `induced` | `imported`), created_at, last_used_at, use_count, success_count.
- `context.py` injects a **skill manifest** (name + description, ~100 tokens each) for enabled skills
  in scope, layered global → project → chat exactly like your instructions/memories.
- One new core tool: `view_skill(name)` returning the body. Add `list_skill_files(name)` /
  `read_skill_file(name, path)` if you support bundled resources (level 3).
- `$name` in the composer force-injects the full body (user-controlled path).
- Bundled `scripts/` execute through `run_python`/`run_agent_code` — your existing sandbox is the
  "Skills architecture" VM. Note Anthropic's API skills also run with **no network and no runtime
  package installation**, which is precisely your sandbox's posture. You're closer to parity than you
  might think.

### 5.4 Agents authoring their own skills, and the research on learned-procedure reuse

Anthropic states the direction explicitly: they anticipate enabling "agents to create, edit, and
evaluate Skills on their own, letting them codify their own patterns of behavior into reusable
capabilities" ([Equipping agents for the real world](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)).
The documented human process is already semi-automatic: do the task without a skill, then *ask the
model* "create a Skill that captures the pattern we just used" — "Claude models understand the Skill
format natively."

The research evidence, with numbers:

| System | Setting | Result |
|---|---|---|
| **[Agent Workflow Memory](https://arxiv.org/abs/2409.07429)** (AWM, 2024) | Mind2Web + WebArena, 1 000+ tasks, 200+ domains | Induces reusable *workflows* from experience and selectively injects them. **+24.6% (Mind2Web) and +51.1% (WebArena) relative success rate**, with **fewer steps** on solved WebArena tasks. Online (supervision-free, learns from its own successes) generalises cross-task/website/domain, **surpassing baselines by 8.9–14.0 absolute points as the train-test gap widens**. Also beats SteP, which uses *human-written* workflows, by 7.6% relative. |
| **[Voyager](https://arxiv.org/abs/2305.16291)** (2023) | Minecraft, lifelong learning | Skill library of executable code fragments, **indexed by embedding of the skill description**, retrieved in similar situations. **3.3× more unique items, 2.3× longer distances, tech-tree milestones up to 15.3× faster** (stone tools 8.5×, iron 6.4×) than prior SOTA. Skill library **transfers to a fresh world** to solve novel tasks where baselines fail. |
| **[ExpeL](https://arxiv.org/abs/2308.10144)** (2023) | Three domains | Collect success+failure trajectories (via Reflexion) → extract cross-task natural-language insights → recall at test time. Consistent gains over strong baselines and **positive forward transfer** source→target. Weaker evidence: no single headline number; gains are domain-dependent. |
| **[ReUseIt](https://arxiv.org/abs/2510.14308)** (2025) | 15 repetitive web tasks | Synthesised reusable workflows raised success from **24.2% → 70.1%**. |
| **[SkillOps](https://arxiv.org/html/2605.13716v1)** (2026) | ALFWorld | Treats the library itself as the object of maintenance (merge redundant, repair, retire, add validators/adapters). **79.5% standalone success, +8.8 pp over the strongest baseline, with zero additional task-time LLM calls.** Holds **80.5% as the library grows 200 → 2 000 skills** under increasing degradation. Task-time token cost neutral-to-negative (decreased in 24 of 35 conditions). |

**The consistent finding across all of them:** reusing an induced procedure improves success rate
*and* reduces steps/tokens. The consistent *second* finding — and the one nobody implements — is that
libraries rot. SkillOps names the failure modes: redundancy, missing validators, interface drift,
stale implementations. Plan for maintenance from day one (see the feature table).

**Concrete induction loop for Personal OS** (you already have every ingredient):

1. After a chat ends (or on demand: a "save as skill" button on the last message), run
   `deepseek-v4-flash` over the transcript + `tool_events` with a fixed extraction prompt.
2. Condition on evidence of success: ≥3 tool calls, no unresolved errors in the last round, and
   either explicit user approval or no corrective follow-up. (AWM's online mode uses exactly this
   kind of self-labelled success signal.)
3. Emit a candidate SKILL.md: name (gerund), `description` with a "Use when…" clause, a numbered
   workflow naming the actual tools used, and the parameters that varied.
4. **Never auto-enable.** Put it in a review queue in the UI — the user names it, edits it, enables
   it. This is also the security answer: an induced skill is model-authored text that will later be
   injected into the system prompt, i.e. a self-prompt-injection vector if auto-trusted.
5. Track `use_count` / `success_count` per skill; surface low-value and redundant skills for
   retirement (SkillOps' library loop, done by hand at your scale).

---

## 6. Composition and reuse

- **MCP prompts are the standard for user-invoked, parameterised procedures.** They're
  user-controlled by design and surface as slash commands in hosts. If you implement MCP, wire
  `prompts/list` + `prompts/get` into a `/` menu in your composer. Note `prompts/get` also supports
  `InputRequiredResult`, so a prompt can ask for its own arguments through your elicitation form.
- **Claude Code has converged slash commands and skills**: as of 2026, "slash commands and skills are
  unified, with every skill getting a `/slash-command` interface"; user commands live in
  `.claude/commands/<name>.md` with a Markdown body as prompt template and optional frontmatter for
  description, allowed tools and default model
  ([Claude Code docs](https://code.claude.com/docs/en/skills)). **Do the same thing:** one `skills`
  table, two entry points — auto-triggered by description match, or explicitly invoked as `/name`.
  Don't build "saved prompts" and "skills" as separate features; that's the mistake the ecosystem
  already made and corrected.
- **Subagent definitions are the same file format one level up.** Claude Code subagents are markdown
  files in `.claude/agents/` with frontmatter (`description`, `tools`, `disallowedTools`, `model`,
  `permissionMode`, `mcpServers`, `maxTurns`, `skills`, `memory`, …) and the body as system prompt.
  Even though Track B isn't subagents, the *format* matters: if your skill frontmatter allows
  `tools:` and `model:`, then a skill is already a parameterised agent recipe — "run this procedure
  with only these 4 tools on the cheap model." That's a natural way to cap the blast radius of an
  induced skill, and it dovetails with the `ask`-escalation rule in §4.7.
- **Parameterisation**: keep it simple — `$ARGUMENTS`-style substitution in the body plus a
  frontmatter `args:` list using the same flat-primitive schema MCP elicitation uses, so the same
  form renderer serves both.
- **"Agent-authored automations"**: the induction loop in §5.4 plus your already-roadmapped scheduled
  tasks is the full feature. A skill with a `schedule:` frontmatter field *is* a morning brief.

---

## 7. Tool result artifacts: handles, references, files, images, streaming

### 7.1 Handles instead of blobs

MCP 2026-07-28 added a whole non-normative section on **Stateful Tools** that reads like a design
guide for this ([tools spec](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)):
since the protocol has no session, servers "should return an explicit handle from a creation tool and
accept that handle as an argument on subsequent calls." The design advice transfers directly:

- **Authorization:** "a handle is a name, not a capability" — validate on every call.
- **Opacity:** handles that encode internal structure invite guessing.
- **Lifetime:** state the retention policy *in the creation tool's description* so the model can see
  it ("results expire after 1 hour").
- **Expiry errors** must say so, so the model can recover by re-running.

**Personal OS implementation.** Add a `tool_results` table (id, chat_id, tool, args_hash, payload
JSON/blob, rows, bytes, created_at, expires_at). Any tool result above a threshold (say 2 000 tokens)
returns:

```
<result tool="gmail_search" handle="tr_9f21" total="184" shown="5" expires="1h">
…5 summarised rows…
Use result_page(handle="tr_9f21", offset=5) or result_query(handle="tr_9f21", jq=".[] | select(.from|test(\"acme\"))")
</result>
```

Two new core tools — `result_page(handle, offset, limit)` and `result_query(handle, expr)` — replace
a truncation cliff with a navigable dataset, and `result_query` can run in-process (jq/JMESPath) with
no sandbox at all. This is strictly cheaper than code-mode and gets a large share of the same benefit
for the "big list, need three rows" case. **Ship this before code-mode.**

### 7.2 Resource links: the MCP wire format for the same idea

A tool MAY return `{"type":"resource_link","uri":…,"name":…,"mimeType":…}` instead of inlining
content; the client fetches via `resources/read` only if needed. Content blocks also support
`annotations` with `audience` (`["user"]` vs `["assistant"]`) and `priority` — i.e. the protocol
already distinguishes **"show this to the human"** from **"put this in the model's context."** That
distinction is the missing primitive in your tool envelope. Adopt it internally:

```python
ToolResult(
  model_content="Found 184 emails from Acme. Top 5: …",   # goes to the LLM
  user_artifacts=[Artifact(kind="table", data=…, title="Acme mail")],  # goes to the UI only
  handle="tr_9f21",
)
```

Now a chart, a rendered table, a generated file, or a 3 MB JSON blob can reach the user's Artifacts
panel at **zero context cost**. Implementation warning from the field: several clients silently drop
`resource_link`-only results and hand the agent an empty tool result
([agentscope#2798](https://github.com/agentscope-ai/agentscope/issues/2798)) — when you consume MCP,
always render links *and* synthesise a text summary so the model never sees an empty result.

### 7.3 Images and files from tools

MCP tool results carry `image` (base64 + mimeType) and `audio` content types alongside text. Two
notes for Personal OS:
- Your models via Fireworks may not all be vision-capable. Route image content to `user_artifacts`
  by default and only into model content when the active model advertises vision. Anthropic's own
  skill guidance uses this pattern deliberately ("convert PDF to images, analyse each page image").
- Files produced inside the sandbox should be **moved out to a per-chat artifact directory and
  returned as a handle + path**, not inlined. Anthropic's code-execution post makes the same move
  (`await fs.writeFile('./workspace/leads.csv', …)` then read back later), and it's what makes
  workflows resumable across rounds.

### 7.4 Streaming partial tool output

The state of the art is unsettled — flag this as thin evidence:

- MCP has a first-class progress channel today: a `progressToken` on the request and
  `notifications/progress` flowing back on the originating request's response stream (explicitly
  preserved in 2026-07-28: request-scoped notifications do **not** move to `subscriptions/listen`).
- A proposal for true streaming partial results, **[SEP-2998](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2998)**
  (`notifications/tools/partial_result`, capability-gated, final `CallToolResult` remains
  authoritative), was **closed unmerged on 2026-09-22** pending Working Group development. So: not
  standard, don't depend on it.
- A widely-cited practical heuristic from the community: **<1 s synchronous; 1–30 s progress
  notifications / streamed partial results; >30 s async job + polling.** Matches MCP's new
  poll-based tasks extension.
- Known UX failure in the wild: hosts show a bare spinner and drop `notifications/progress` entirely
  ([claude-code#51713](https://github.com/anthropics/claude-code/issues/51713)).

**For Personal OS:** you already stream SSE. Emit a `tool_progress` SSE event type (tool, call_id,
message, fraction) and render it in the tool card. Keep progress **out of the model's context** —
it's `audience: ["user"]`. And the >30 s case is the real one for you: a long MCP call inside a
single HTTP SSE response is exactly the fragility the brief already names ("every agent turn dies
with the HTTP connection"). The handle pattern (§7.1) plus a polled job is the right shape.

---

## 8. Where the evidence is thin or contested

- **98.7% token reduction** (code execution with MCP) is one self-reported example, not a benchmark.
  The replicated numbers are CodeAct's +20% / −30% steps and Anthropic's PTC −37% tokens. OpenAI
  pointedly refuses to quote a number and tells you to A/B it.
- **Tool-search accuracy gains (49%→74%)** are Claude-model, server-side, on Anthropic's internal MCP
  evals. Unverified for kimi-k3. The *retrieval-first* variant (RAG-MCP 13.62%→43.13%) is
  peer-reviewable and model-agnostic — prefer citing that when justifying the work internally.
- **The "~50 tools → 84–95%, ~200 → 41–83%, ~740 → 0–20%" ladder** circulating in blog posts traces
  to secondary write-ups; I could not verify it against a primary table within budget. The primary,
  citable thresholds are Anthropic's "30–50" and OpenAI's "fewer than 20 at the start of a turn."
- **ExpeL** has no clean headline number; its claim is "consistent enhancement" plus forward transfer.
- **SkillOps / SAGE / skill-evolution papers (2026)** are recent and, as far as I can tell,
  not independently replicated. Their *taxonomy* of library rot is more useful than their numbers.
- **Streaming partial tool results** is a rejected/pending proposal, not a standard.
- **Induced-skill safety** is essentially unstudied. Auto-enabling model-authored instructions that
  get injected into future system prompts is a self-prompt-injection channel; treat the human review
  gate as non-negotiable until someone publishes on it.

---

## 9. Proposed features

Ordered roughly by (value ÷ effort). Effort: **S** ≤1 day, **M** ~2–5 days, **L** >1 week.

| # | Feature | Effort | Why it matters here |
|---|---|---|---|
| 1 | **Tool-use examples appended to every tool description** (2–3 example arg objects per tool) | S | Anthropic measured 72%→90% on parameter handling; kimi-k3's weakest axis is arg formatting, not tool choice. |
| 2 | **`response_format: concise\|detailed` on all 8 search/read tools**, default concise | S | 206→72 tokens per call in Anthropic's own example; multiply by 8 rounds. |
| 3 | **Error-message rewrite pass**: no tracebacks, name the field + constraint + a corrected example, list near-miss ids, make permission denials steer to an allowed alternative | S | MCP spec: execution errors SHOULD go to the model for self-correction. Turns dead turns into recovered ones. |
| 4 | **Stop injecting tools resolved to `off`**; strip schema noise (`title`, redundant defaults) | S | Free tokens on every round with zero behaviour change. |
| 5 | **Pagination + `has_more`/`total` on list-shaped tools**, replacing the 24 000-char hard truncation | S | Silent truncation is currently losing data the model doesn't know is missing. |
| 6 | **Tool consolidation: 25 → ~14** (`todos(action)`, `board(action)`, `gmail_find(include_body)`, drop `current_time` into the system prompt) | M | Anthropic's #1 tool-design rule; buys you headroom under the 30–50 cliff before MCP arrives. |
| 7 | **Result handles + `result_page` / `result_query`**, `tool_results` table with TTL | M | Converts the biggest context sink into a navigable dataset; cheaper than code-mode and a prerequisite for it. |
| 8 | **`ToolResult(model_content, user_artifacts, handle)` envelope** with an `audience` split | M | Lets charts/tables/files/images reach the Artifacts panel at zero context cost; mirrors MCP's `annotations.audience`. |
| 9 | **Skills v1**: `skills` table, SKILL.md-compatible frontmatter, manifest injection (~100 tok/skill), `view_skill` tool, `$name` force-inject, global/project/chat scoping | M | Open WebUI proves this works on an OpenAI-compatible stack. Turns morning brief / email triage / weekly review into text files instead of 6 more tool schemas. |
| 10 | **Slash-command surface for skills** (`/name`, args via a flat-primitive form) — one feature, not two | S (on top of #9) | Claude Code converged commands and skills in 2026; don't rebuild the split they removed. |
| 11 | **Eager tool pre-selection**: FTS5 BM25 over tool name/description/arg-names on the user's message; inject core set + top-K | M | RAG-MCP: 13.62%→43.13% selection accuracy, >50% prompt-token cut, and requires no cooperation from a weak model. |
| 12 | **`find_tools` escape hatch + per-chat `active_tools` set**, core tools in a fixed prefix order to preserve prefix caching | M | Anthropic's deferred-loading design, client-side. Becomes mandatory the day MCP lands. |
| 13 | **Skill induction from successful chats** (deepseek-v4-flash extraction → review queue → user names/edits/enables; never auto-enable) | M | AWM: +24.6%/+51.1% relative success from exactly this; you already persist transcripts and `tool_events`. Review gate is also the security control. |
| 14 | **Skill library hygiene**: `use_count`/`success_count`, redundancy flagging, retire/merge prompts | S | SkillOps shows libraries rot; holding 80.5% success from 200→2 000 skills required explicit maintenance. |
| 15 | **MCP client v1 — stdio only**, single owning task in FastAPI lifespan + inbox queue, `ClientSessionGroup` with a `component_name_hook` prefixing your own connection id, tools/prompts/resources all wired, schema-hash pinning | L | The anyio cancel-scope failure mode is the #1 reported MCP-in-Python bug; designing around it up front saves days. stdio-only avoids OAuth entirely for v1. |
| 16 | **Permission UI for MCP**: show the tool **description** in the approval card; annotations propose but never decide a `danger` level; MCP tools capped at `always_chat`; re-prompt on schema-hash change | M | Tool poisoning hides in descriptions; rug pulls change them after approval. Both documented in the wild. |
| 17 | **Lethal-trifecta guard**: if the chat has ingested untrusted content this turn, escalate all `external` tools to `ask` regardless of standing grants | S | The only mitigation the literature endorses. Cheap, deterministic, uses `tool_events` you already emit. |
| 18 | **Elicitation support** (form mode; flat-primitive schema → auto-generated form; accept/decline/cancel) reusing the approval card | M | MRTR retry semantics fit your SSE loop cleanly; `cancel` gives your 600 s timeout proper semantics. Skip Sampling and Roots — both deprecated in 2026-07-28. |
| 19 | **Code-mode (`run_agent_code`)**: stub module tree in the sandbox + AF_UNIX broker on the host enforcing permissions/approvals/quotas; one narrow hole in the `sandbox-exec` profile | L | CodeAct +20% success / −30% steps; PTC −37% tokens. The broker — not `allowed_callers`-style hints — must be the security boundary. |
| 20 | **Tool-eval harness**: ~20 realistic multi-step tasks against a frozen SQLite snapshot, scoring accuracy, tool calls, tokens, errors, runtime | M | Anthropic's whole methodology depends on it, and you already emit every metric it needs via `trace.py`/`usage.py`. Without it, every item above is a guess. |
| 21 | **`tool_progress` SSE event** rendered on the tool card, `audience: user` only | S | Long MCP/web calls currently look like a hang; progress is standard MCP and free to surface. |
| 22 | **Adopt the `.mcpb` `manifest.json` schema for server install/config** (auto-generated settings UI, sensitive-field handling) + browse the official MCP Registry | M | Don't invent a config-UI format or hand-maintain a server list; both already exist and are stdio/offline-first, like you. |

**Suggested sequencing:** 1–5 (a day, pure win) → 6–8 (tool surface and results) → 9, 10, 13, 14
(skills; the biggest product-level unlock) → 20 (evals, before anything risky) → 11, 12 (scaling,
just before MCP) → 15–18 (MCP with the permission model) → 19 (code-mode) → 21, 22 (polish/ecosystem).

---

## Sources

**Anthropic engineering & docs**
[Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) ·
[Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) ·
[Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) ·
[Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) ·
[Equipping agents with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) ·
[Tool search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool) ·
[Programmatic tool calling](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling) ·
[Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) ·
[Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) ·
[Claude Code skills](https://code.claude.com/docs/en/skills) ·
[Build a desktop extension with MCPB](https://claude.com/docs/connectors/building/mcpb)

**MCP specification & ecosystem**
[2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog) ·
[Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) ·
[Elicitation](https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation) ·
[Multi Round-Trip Requests](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr) ·
[Tool annotations as risk vocabulary](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/) ·
[SEP-2998 partial tool results](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2998) ·
[Python SDK session groups](https://py.sdk.modelcontextprotocol.io/client/session-groups/) ·
[python-sdk#521 cancel scopes](https://github.com/modelcontextprotocol/python-sdk/issues/521) ·
[langchain-mcp-adapters#466](https://github.com/langchain-ai/langchain-mcp-adapters/issues/466) ·
[github/github-mcp-server toolsets](https://github.com/github/github-mcp-server) ·
[MCPB repo](https://github.com/modelcontextprotocol/mcpb)

**Security**
[Invariant: tool poisoning attacks](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks) ·
[Invariant: MCP-Scan](https://invariantlabs.ai/blog/introducing-mcp-scan) ·
[Simon Willison: the lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/) ·
[Checkmarx: MCP incidents & controls](https://checkmarx.com/learn/mcp-security-risks-real-world-incidents-and-security-controls/) ·
[UpGuard: six MCP security incidents](https://www.upguard.com/blog/mcp-security-incidents) ·
[Nudge: Asana MCP exposure](https://www.nudgesecurity.com/post/asana-mcp-server-data-exposure-incident)

**Papers**
[CodeAct 2402.01030](https://arxiv.org/abs/2402.01030) ·
[Agent Workflow Memory 2409.07429](https://arxiv.org/abs/2409.07429) ·
[Voyager 2305.16291](https://arxiv.org/abs/2305.16291) ·
[ExpeL 2308.10144](https://arxiv.org/abs/2308.10144) ·
[RAG-MCP 2505.03275](https://arxiv.org/abs/2505.03275) ·
[MCP-Zero 2506.01056](https://arxiv.org/abs/2506.01056) ·
[How Many Tools Should an LLM Agent See? 2605.24660](https://arxiv.org/abs/2605.24660) ·
[LiveMCPBench 2508.01780](https://arxiv.org/abs/2508.01780) ·
[ReUseIt 2510.14308](https://arxiv.org/abs/2510.14308) ·
[SkillOps 2605.13716](https://arxiv.org/html/2605.13716v1)

**Other**
[OpenAI function calling guide](https://developers.openai.com/api/docs/guides/function-calling) ·
[OpenAI programmatic tool calling](https://developers.openai.com/api/docs/guides/tools-programmatic-tool-calling) ·
[smolagents intro](https://huggingface.co/blog/smolagents) ·
[Open WebUI skills](https://docs.openwebui.com/features/workspace/skills/) ·
[LiteLLM MCP](https://docs.litellm.ai/docs/mcp) ·
[Simon Willison on code execution with MCP](https://simonwillison.net/2025/Nov/4/code-execution-with-mcp/)
