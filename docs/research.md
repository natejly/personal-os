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
| L17 | Keychain token storage, per-source model routing to a local model, full export | M | |

Suggested order: L1, L2, L3, L4, L5, L6, L7, L8, L9, L10, L11, L12, L13, L14, L17, L15, L16. Approval and undo first, because everything after writes through them.

---

## Combined next steps

1. **Cheap wins this week:** L2 write journal and undo, M1 provenance, M2 reconciliation, M3 decay, A3 cost tracking, R2 reranking, R4 token budget.
2. **Next:** L1 approval queue, L3 scheduled brief, L4 heartbeat, R1 hybrid retrieval, A2 conversation tree, M4 memory blocks with a nightly rewrite job.
3. **Then:** L5 quick capture, L7 email triage, L8 pre-meeting briefs, A5 MCP client, A8 scheduled tasks, R3 chunker, R5 citations.
4. **Later:** L9 people memory, L11 time blocking, M6 bi-temporal graph, A9 tape and compaction, A11 artifacts, A13 packaging, A12 voice, L16 meeting recap.
