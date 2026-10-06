## Memory, knowledge graph and writing-style learning

### Where we are

**Memories.** `memories` table (db.py SCHEMA): `id, project_id (NULL = personal), content, kind fact|preference|goal|note, source user|auto, pinned, created_at, updated_at`, with an FTS5 mirror `memories_fts` (porter unicode61) maintained by hand in `repos.Memories.create/update/delete`. No provenance (which message produced a row), no confidence, no validity interval, and no embeddings anywhere in the backend (llm.py, docs.py and repos.py contain no embed calls). `Memories.create` dedups only on exact `lower(content)`.

**Write path.** `learn.learn_from_exchange` runs after `done` via `LearnWorker` (serial queue, depth 32, publishes `learned` on the app topic). One extraction call sees the 60 most relevant memories from `Memories.for_context`, tagged `[M1]..[Mn]`, plus the exchange (user 4000 chars, assistant 3000), and returns `memories / updates / forget / entities / relations`. `updates` overwrite content in place (history lost). `forget` hard-deletes unless pinned. There is no NONE decision and no similarity retrieval of candidates, so contradictions with memory #61+ are never seen and become near-duplicates. The prompt already has a durability test, user-said-only, preference faithfulness and no-entity-for-the-user. Relative dates ("next month") are stored verbatim and rot.

**Read path.** `context.build_context` injects `Memories.for_context` (pinned+recent up to 40, unioned with 15 BM25 hits from an OR-query on the user message) as a flat bullet list. `Graph.neighborhood` seeds from node labels that are substrings/words of the query, then takes 1 hop; it loads the whole graph into Python each turn. Tools: `search_memory` (FTS), `graph_search`, `graph_traverse`, `save_memory`, `graph_add` (tools.py ~564-640). Per-chat `useMemory/useGraph` toggles.

**Graph.** `kg_nodes(label, type, properties)` unique on `(project_id, lower(label))`; `kg_edges(source_id, target_id, relation, properties)` unique on `(source, target, lower(relation))`. Entity resolution is exact-label only ("Postgres" vs "PostgreSQL" vs "Dr. Smith" vs "John Smith" become separate nodes). Edges have no timestamps beyond `created_at`, so "works at X" and later "works at Y" both stay true forever. No provenance, no edge fact text, no node summaries, no communities.

**Style.** style.py is strong: verbatim evidence samples (prose filter, dedup by ref, 80/scope), one profile per scope (summary, guidelines, traits, phrases, avoid), lazy relearn every 3 samples, `edited` freezes auto-relearn, injected only for drafting. Weak spots: profile is one global voice per scope, not per register (email vs chat vs doc); no few-shot exemplar injection; no measured stylometrics computed locally (traits are LLM impressions); no evaluation of whether a draft matches.

**Adjacent, already shipped (do not respec).** Skills induction with approve-only gating (learn.Skills, skillbuild.py), activity-habit memories with confidence (insights.py), async LearnWorker, working-memory plan/handles, taint tracking for tool results, pinned memories (user-curated, extractor cannot drop). docs/research/roadmap.md items M2 (ADD/UPDATE/DELETE reconciliation) is partly shipped as updates/forget; M1 provenance, M3 temporal, M4 sleep-time rewriter, M5 hybrid retrieval, M8 linking are NOT shipped.

**Weak spots, ranked by user-visible damage:** stale/contradicted facts live forever or are destroyed without trace; candidate set for dedup is just recency; retrieval is lexical only so "my boss" never finds "manager Priya"; duplicate entities fragment the graph; nothing consolidates over time so the list grows into noise; no way to ask "why do you believe this".

### What the best open-source systems do

**Mem0** ([prompts.py](https://raw.githubusercontent.com/mem0ai/mem0/main/mem0/configs/prompts.py), [paper](https://arxiv.org/html/2504.19413v1)). Extraction prompt: 15-80 word self-contained memories, preserve proper nouns/quantities, convert relative time to absolute using the observation date, skip anything already in existing/recent memories, never re-extract assistant echoes. Update phase: embed each new fact, retrieve top-k similar existing memories, one LLM call returns `{memory:[{id,text,event ADD|UPDATE|DELETE|NONE,old_memory}]}`; ids are remapped to small integers to prevent hallucinated ids. Graph variant extracts triples, merges entities by embedding similarity, marks contradicted relations invalid rather than deleting.

**Letta / MemGPT** ([sleep-time compute](https://www.letta.com/blog/sleep-time-compute/), [agent memory](https://www.letta.com/blog/agent-memory/)). Core memory blocks (`label, description, value, char_limit`) always in context, edited by tools. A second sleep-time agent shares those blocks and runs asynchronously between sessions to consolidate "messy, incremental" memories into clean ones, moving compute off the latency path.

**Graphiti / Zep** ([paper](https://arxiv.org/pdf/2501.13956), [edge invalidation](https://blog.getzep.com/beyond-static-knowledge-graphs/)). Episodes (raw messages) feed entity extraction, entity resolution against existing nodes (embedding + full-text candidates, then LLM dedupe), fact extraction as edges carrying a natural-language `fact`, and fact resolution. Edges are bi-temporal: `created_at/expired_at` (system time) and `valid_at/invalid_at` (world time). A new edge is compared with similar existing edges between the same nodes; contradicted ones get `invalid_at` set to the new edge's `valid_at` and are never deleted. Retrieval is hybrid: cosine + BM25 + BFS from seed nodes, fused (RRF/MMR/cross-encoder), no LLM at query time.

**Cognee** ([repo](https://github.com/topoteretes/cognee)). remember/recall/improve/forget: text becomes entities and relationships, optional ontology grounding, automatic routing between graph, vector and keyword search (keyword-only when no LLM configured), and an `improve` pass that bridges session knowledge into the permanent graph and applies feedback.

**LangMem** ([concepts](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/)). Semantic memory as collections (reconcile insert/update/delete) or profiles (one document updated in place); episodic and procedural memory; a hot-path vs background ("subconscious") memory manager; a prompt optimizer that rewrites instructions from trajectories.

**A-MEM** ([paper](https://arxiv.org/html/2502.12110v2)). Each memory is a Zettelkasten note: content, timestamp, LLM keywords, tags, contextual description, embedding of all of them, links. On insert: cosine top-k neighbors, LLM decides links, and neighbors' context/keywords/tags may be rewritten ("memory evolution").

**Hindsight** ([paper](https://arxiv.org/pdf/2512.12818)). Separates world facts, experiences, observations and opinions-with-confidence; retain/recall/reflect operations; recall runs semantic, BM25, graph and temporal strategies and fuses them. Reported to beat Zep and Mem0 on LongMemEval (numbers from the paper as cited in docs/research/roadmap.md).

**ChatGPT / Claude consumer memory** (as documented in docs/research/roadmap.md): background "dreaming" that rewrites time-sensitive memories, editable per-topic summaries.

**Style learning.** No open-source memory system has serious style learning; the closest practice is few-shot exemplar retrieval of the user's own text plus measured stylometrics. style.py already leads the memory systems here; the remaining gap is exemplars and per-register profiles.

### Gaps

| # | Gap | Who does it | Impact | Effort |
|---|---|---|---|---|
| 1 | Supersede instead of overwrite/delete: validity interval + provenance on memories and edges, history view, "why do I believe this" | Graphiti (bi-temporal), Mem0 graph invalid flag, Hindsight | High: stops silent loss and stale facts, enables audit and undo | M |
| 2 | Semantic retrieval + candidate retrieval for reconciliation (embeddings, RRF with BM25/recency/graph), graceful FTS-only fallback | Mem0, Graphiti, A-MEM, Hindsight, Cognee | High: fixes paraphrase misses and the 60-row blind spot in dedup | M |
| 3 | Background consolidation pass producing reviewable proposals (merge dupes, absolutize dates, entity merge, summarize) | Letta sleep-time, ChatGPT dreaming, Cognee improve | High: keeps memory from rotting at scale | M |
| 4 | Entity resolution (alias/embedding merge) | Graphiti, Mem0 graph | Med-High: graph fragments today | S-M (inside #3) |
| 5 | Absolute dates at extraction time | Mem0 | Medium | S (inside #1) |
| 6 | Memory notes with keywords/tags/links and neighbor evolution | A-MEM | Medium | M |
| 7 | Typed stores with confidence (opinions vs facts) | Hindsight | Medium | M |
| 8 | Core blocks edited via tools (profile / project-state) | Letta, LangMem profiles | Medium | M |
| 9 | Style: exemplar retrieval, per-register profiles, local stylometrics | none in OSS memory; practice from writing assistants | Medium | M |
| 10 | Graph communities / global summaries | GraphRAG, Cognee | Low for personal-scale graph | L |

### Build next

**memory-1 (bi-temporal supersession and provenance).** Add validity and provenance columns to memories and kg_edges, make learn.py's `updates`/`forget` and contradiction handling soft (invalidate, link `superseded_by`) instead of overwrite/delete, filter retrieval to currently valid rows, give the extractor the observation date so relative times become absolute, and add a History toggle plus "why" provenance in MemoryView. This is the cheapest high-value change and every later spec depends on rows being non-destructive.

**memory-2 (hybrid retrieval with optional embeddings).** New module `memory_index.py`: an optional `llm.embed` (LiteLLM, settings key `embeddingModel`, empty = off), a `memory_vectors` table, pure-stdlib cosine, and reciprocal-rank fusion of BM25, vector, recency/pin and graph-seed rankings. Used by `context.build_context`, `search_memory`, and as the candidate set for the extractor so reconciliation sees the semantically nearest memories rather than the 60 most recent. Falls back to today's behavior with no embedding model.

**memory-3 (consolidation proposals, "dream pass").** New module `consolidate.py` plus a `memory_proposals` table: an on-demand (and optionally post-N-writes, never a heartbeat) pass that proposes merging near-duplicate memories, rewriting time-relative ones, and merging duplicate graph entities. Nothing applies without user approval in the Memory view; pinned rows are never touched; the model only sees memory text and returns ids, never acts externally. Style stays as is this round; gap 9 is next after these.

### Specs

#### memory-1: Non-destructive memory: validity intervals, supersession and provenance (M)

**Why.** Graphiti keeps bi-temporal edges and invalidates instead of deleting; Mem0's graph marks contradicted relations invalid and its prompt absolutizes relative dates. Today learn.py overwrites on `updates` and hard-deletes on `forget`, so history and provenance are lost and stale edges (works at X, then Y) both stay true. Everything else (consolidation, undo, audit) needs non-destructive rows first.

**Files.** `backend/personal_os/db.py`, `backend/personal_os/repos.py`, `backend/personal_os/learn.py`, `backend/personal_os/context.py`, `backend/personal_os/tools.py`, `backend/personal_os/app.py`, `src/renderer/src/components/MemoryView.tsx`, `src/renderer/src/components/MemoryPanel.tsx`, `backend/tests/test_memory_temporal.py`

**Design.** Migration (db._migrate `wanted` dict style): memories += {valid_from REAL, invalid_at REAL, superseded_by TEXT, source_conversation_id TEXT, source_message_id TEXT}; kg_edges += {valid_at REAL, invalid_at REAL, superseded_by TEXT, source_message_id TEXT, fact TEXT NOT NULL DEFAULT ''}. Add index idx_mem_valid ON memories(project_id, invalid_at) via c.execute after the loop (like idx_runs_desk). Existing rows keep NULL = valid. memories_fts: on invalidation delete the FTS row so search skips it; restore re-inserts.

repos.Memories: add `supersede(old_id, new_content, kind=None, source='auto', provenance=None) -> new row` (creates new row with valid_from=now, sets old.invalid_at=now, old.superseded_by=new.id, drops old from FTS); `invalidate(id)` (soft forget, no replacement); `restore(id)`; `history(id)` returns the chain; `create(..., provenance: dict|None)` stores source_conversation_id/source_message_id; `list(..., include_invalid=False)` and `for_context` filter `invalid_at IS NULL` by default. Pinned rows: supersede/invalidate raise or no-op for source='auto' callers (preserve existing 'extractor never drops pinned' rule; extractor may still supersede pinned only via content update as today, keep behavior: pinned stays valid, update rewrites in place).

repos.Graph: `upsert_edge(..., valid_at=None, source_message_id=None, fact='')`; `invalidate_edge(id, at=None, superseded_by=None)`; `get()` and `neighborhood()` drop edges with invalid_at not NULL unless `include_invalid=True`. Unique index idx_edge_uniq stays; an invalidated edge that is re-asserted is revived (clear invalid_at) rather than duplicated.

learn.py: EXTRACT_PROMPT gains (a) a `Today is {date}` line and rule 'convert relative dates (tomorrow, next month) to absolute dates using today; keep the original wording only if no date can be inferred'; (b) relation objects gain optional `"replaces": "<existing relation as 'Source|relation|Target'>"`, and an `"ended": [{source,target,relation}]` array for relations the user says no longer hold. learn_from_exchange gets optional `conversation_id`, `message_id` params (LearnJob already has both; pass them through in LearnWorker._run) and uses Memories.supersede for `updates`, Memories.invalidate for `forget`, Graph.invalidate_edge for `ended`/`replaces`. Result dict gains `superseded` and `invalidated` lists so the `learned` event can drive an Undo toast.

Routes (small additions in app.py near /memories): GET /memories gains `include_invalid=false`; POST /memories/{id}/restore; GET /memories/{id}/history; GET /graph gains include_invalid. tools.py: `search_memory` unchanged but results include `valid_from`; add nothing else (keep edits tiny).

UI: MemoryView gets a 'Show history' toggle; superseded rows render struck-through with 'replaced by ...' and a Restore button; each auto memory shows a 'from chat' link using source_conversation_id. MemoryPanel unchanged except hiding invalid rows (default API already does).

No new settings keys.

**Tests.** backend/tests/test_memory_temporal.py, plain script like test_learn.py (stub learn.llm.complete via monkeypatch, tempfile Database). Cases: (1) supersede keeps old row with invalid_at and superseded_by, old row absent from for_context/list/FTS search, present with include_invalid; history chain order correct; restore revives and re-indexes FTS. (2) learn_from_exchange with stubbed `updates` produces a superseded old row (not overwritten), `forget` invalidates not deletes, pinned row survives forget. (3) provenance: memory created via learn carries source_message_id. (4) Edge: upsert 'Nate|works at|Acme', then stub reply `ended` for it plus new 'works at Beta': Acme edge invalid, neighborhood returns only Beta; re-asserting Acme revives it. (5) migration: open a Database created with the old schema (create tables without new columns via sqlite3, then Database(tmp)) and assert columns added and old rows still valid. (6) prompt contains today's date string (assert on messages captured by the stub). Run existing test_learn.py and test_working_memory.py unchanged.

**Done when.** A contradicted preference or relation is no longer injected into context or returned by search_memory/graph_search, but remains visible under Show history with its replacement and can be restored; auto-learned memories link to their source conversation; relative dates in new auto memories are absolute; extractor still cannot drop pinned memories; existing tests/test_learn.py passes; migration is idempotent on an existing DB.

#### memory-2: Hybrid memory retrieval: optional embeddings fused with BM25, recency and graph via RRF (M)

**Why.** Mem0, Graphiti, A-MEM, Hindsight and Cognee all retrieve semantically and fuse with lexical/graph signals; Mem0 and Graphiti also use the nearest existing items as the candidate set when deciding add/update/invalidate. We have FTS-only retrieval and feed the extractor just the 60 most recent rows, so paraphrases are missed and contradictions with older memories become duplicates. Must degrade gracefully (no embedding model configured = today's behavior) and add no heavy dependency.

**Files.** `backend/personal_os/memory_index.py`, `backend/personal_os/llm.py`, `backend/personal_os/db.py`, `backend/personal_os/repos.py`, `backend/personal_os/context.py`, `backend/personal_os/learn.py`, `backend/personal_os/tools.py`, `backend/personal_os/app.py`, `backend/tests/test_memory_index.py`

**Design.** New module memory_index.py (no app.py growth beyond one wiring line).

Schema (own SCHEMA string executed in `MemoryIndex.__init__` like learn.Skills does): memory_vectors(memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE, model TEXT NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL, content_hash TEXT NOT NULL, updated_at REAL NOT NULL). Vectors stored as float32 via stdlib `array('f')`.tobytes(), L2-normalised on write so cosine = dot product.

llm.py: add `async def embed(settings, texts: list[str]) -> list[list[float]]` calling litellm.aembedding with settings['embeddingModel'] (note repo gotcha: a LiteLLM proxy embedding model needs `mode: embedding` under model_info, not litellm_params); batch 32; raises on failure. Add `"embeddingModel": ""` (empty = off) and `"hybridRetrieval": True` to llm.DEFAULT_SETTINGS or PUT /settings will drop them.

memory_index.py API: `class MemoryIndex(db, embed_fn=llm.embed)`; `async def index(settings, memory_ids|None)` embeds rows whose content_hash differs from stored (idempotent backfill, max 200 per call); `def search(project_id, query, query_vec|None, limit, include_scope) -> list[row]` does reciprocal rank fusion, score = sum 1/(60+rank) over up to four rankers: BM25 (existing memories_fts), cosine over memory_vectors (brute-force in Python, cap 5000 rows, skipped when query_vec is None or model mismatch), recency/pin (pinned first then updated_at), and graph-seed (memories whose content mentions a label returned by Graph.neighborhood). Pure function `rrf(rankings: list[list[id]], k=60) -> list[id]` exported for tests. Never raises: embedding errors log and fall back to the FTS+recency rankers.

Wiring: `Memories.for_context` stays as the sync fallback; context.build_context takes optional `index` and, when `settings['hybridRetrieval']` and index present, uses a precomputed `query_vec` (embedding the user query once, awaited by the caller in app.py's context-building path, with 2s timeout, falling back to None). learn.learn_from_exchange gets optional `index`: candidates for the [M1..] list become the top 20 by hybrid search for user_text unioned with pinned/recent up to 40 (replacing 60-by-recency), then new/updated memories are indexed after write. tools.search_memory uses index.search when available. Add `POST /memories/reindex` (backfill, returns count) in app.py. Optional extra: none; `numpy` is NOT required.

Settings UI: only a text field for `embeddingModel` under the Knowledge Base / Memory settings tab (existing pattern); no other UI.

**Tests.** backend/tests/test_memory_index.py, offline. Use a deterministic fake embed_fn (e.g. hash words into a 16-dim bag-of-words vector, so 'manager' and 'boss' can be forced close by a hand-built synonym table). Cases: (1) rrf ordering and tie behavior on hand-made rankings. (2) with fake embeddings, a query 'who is my boss' retrieves the memory 'Priya is the user's manager' that BM25 alone misses; with embedding off (embeddingModel empty / embed_fn raising) the same call returns the BM25+recency result without raising. (3) index() is idempotent (second call embeds nothing, count via call counter), re-embeds after Memories.update changes content, and a deleted memory cascades its vector. (4) model mismatch rows are ignored. (5) learn_from_exchange passes the semantically nearest memory into the prompt candidates even when it is older than 60 newer rows (capture messages in stubbed llm.complete and assert the tag list contains it). (6) context.build_context with hybridRetrieval False is byte-identical to current output (regression guard against test_working_memory/test_learn fixtures). Run existing test_learn.py, test_learn_async.py.

**Done when.** With embeddingModel unset the app behaves exactly as before and no network/embedding call is attempted; with it set, memories are lazily embedded, paraphrase queries retrieve the right memory in context and search_memory, the extractor's candidate list includes semantically similar old memories, and all new tests pass offline with a fake embed function.

#### memory-3: Consolidation proposals: dedupe, de-rot and entity merge, approve-only (M)

**Why.** Letta's sleep-time agent, ChatGPT's dreaming and Cognee's improve pass all consolidate memory in the background; Graphiti/Mem0 resolve duplicate entities. We have no consolidation, exact-label entity matching only, and relative-time memories that rot. Per owner decisions, anything model-written that changes durable state must be reviewable: this follows the learn.Skills candidate/approve pattern and has no heartbeat.

**Files.** `backend/personal_os/consolidate.py`, `backend/personal_os/db.py`, `backend/personal_os/repos.py`, `backend/personal_os/app.py`, `backend/personal_os/llm.py`, `src/renderer/src/components/MemoryView.tsx`, `backend/tests/test_consolidate.py`

**Design.** New module consolidate.py with its own SCHEMA executed in `Consolidator.__init__(db, memories, graph)`: memory_proposals(id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id) ON DELETE CASCADE, kind TEXT NOT NULL, -- merge_memories | rewrite_memory | merge_entities\n payload TEXT NOT NULL, -- JSON; ids + proposed text/label\n rationale TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', -- pending | applied | dismissed | stale\n created_at REAL NOT NULL, decided_at REAL).

Candidate generation is deterministic and offline-friendly before any LLM call: (a) memory clusters = pairs with token Jaccard >= 0.6 on normalised content, or (if memory-2 index exists) cosine >= 0.88, same project scope, neither pinned; (b) time-rot = auto memories older than 30 days whose text matches a relative-date regex (tomorrow, next week, this month, upcoming, soon, currently); (c) entity pairs = same type and (normalised label equal after stripping punctuation/titles/case, or one label's tokens a subset of the other's, or alias-like edit distance <= 1 for labels >= 6 chars). Cap 20 candidates per run.

`async def propose(settings, project_id, model) -> list[proposal]`: one LLM call per batch of <=10 candidates with a strict JSON schema {proposals:[{kind, ids:[...], text|label, rationale}]} and ids remapped to C1..Cn (Mem0 trick, so hallucinated ids are impossible); validate every id exists, is unpinned, in scope, otherwise drop. Skip candidates that already have a pending or dismissed proposal with the same id set (dismissals are remembered). Prompt states: merge only if same fact, keep the most specific wording, never invent, convert relative dates only when an absolute date is derivable from created_at (passed in), otherwise propose nothing.

`apply(proposal_id)`: merge_memories creates one memory (source='auto') and removes originals, preferring memory-1's Memories.supersede/invalidate when present (check `hasattr`; plain delete fallback); rewrite_memory updates content; merge_entities moves all edges of the loser to the winner (re-point source_id/target_id, dedupe against idx_edge_uniq by skipping conflicts, drop self-loops), merges properties, deletes loser node. Re-validate at apply time: if any referenced row changed or vanished mark status='stale' and do nothing. Apply is only ever called from a user route.

Routes in app.py (small block; or an APIRouter mounted in one line): POST /memories/consolidate {project_id} (runs propose, returns proposals; manual button, plus an optional trigger from LearnWorker after every 25 new auto memories that only *creates* pending proposals and publishes `proposals` on the app topic, no apply), GET /memories/proposals?status=pending, POST /memories/proposals/{id}/apply, POST /memories/proposals/{id}/dismiss. New settings keys in llm.DEFAULT_SETTINGS: `consolidateEvery` (int, default 25, 0 = manual only), uses existing `extractionModel`.

UI: MemoryView gets a 'Tidy up' button and a pending-proposals list with before/after text, Apply and Dismiss. Reuse existing .empty-state and primary-btn styles.

Safety: proposals read only memory and graph text, never act outside the app; pinned memories excluded; no auto-apply path exists.

**Tests.** backend/tests/test_consolidate.py, offline, stub llm.complete. Cases: (1) candidate generation: two memories 'User lives in Austin' / 'User lives in Austin, Texas' cluster; a pinned one is excluded; unrelated ones are not paired; entity pair 'Postgres'/'PostgreSQL' (edit distance) and 'Priya'/'Priya Shah' (token subset) are found, different types are not. (2) propose with a stubbed reply: valid proposals stored pending; a reply referencing an unknown or pinned id is dropped; malformed JSON yields zero proposals without raising; re-running does not duplicate pending ones and does not resurface dismissed ones. (3) apply merge_memories leaves exactly one memory and indexes it in FTS; apply merge_entities re-points edges, drops self-loops and conflicting duplicates, deletes the loser, leaves graph consistent (no dangling edges, unique index intact). (4) stale: edit a referenced memory after proposing, apply returns stale and changes nothing. (5) apply never invoked by propose (assert row counts unchanged after propose). (6) time-rot regex candidates only include auto memories older than 30 days. Plain-script style like test_learn.py.

**Done when.** Pressing Tidy up on a store with duplicate memories and duplicate entities yields reviewable proposals; nothing changes until Apply; applying merges rows and graph edges cleanly with no dangling edges; dismissed proposals do not return; pinned memories are never proposed; tests pass offline with a stubbed model.

