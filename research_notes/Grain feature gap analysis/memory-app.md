# Grain memory, knowledge graph, and writing voice

Inventory of the working tree on 2026-10-02. Code wins over `docs/research/sota-memory.md` and over README passages that still describe the older keyword-only path. Document RAG and activity capture are out of scope except where they share a setting or write a memory row.

Status words used below: **shipped** means a caller in app, tools, or the Memory UI exercises it; **partial** means the column, route, or prompt exists but a stated behavior is missing or weaker than the UI/docs; **absent** means no implementation.

## How are memories extracted, stored, edited, pinned, scoped, forgotten, and retrieved?

### Takeaway

**Shipped.** After a successful chat reply, `LearnWorker` runs one extraction call and writes personal or project memories. Rows live in `memories` with an FTS5 mirror. The user can add, edit, pin, move scope, and forget from Memory. Chats inject a capped set: hybrid rank when an embedding is available, otherwise pinned plus recent unioned with BM25. **Partial:** two different “forget” paths (trash vs invalidate); pin is not a hard guarantee under hybrid rank; the extractor skips pinned rows rather than rewriting them.

### Cited Findings

- Extraction runs only when the reply has text, no error, the run is not proposal-only, the turn is not tainted, global `autoLearn` is true, and the chat’s `autoLearn` is true. The job is queued on `LearnWorker` (depth 32, one job at a time) after `done` is yielded; a full queue drops the job. Results publish `learned` on the app event stream. — [`_chat_stream` auto-learn gate](backend/personal_os/app.py), [`LearnWorker`](backend/personal_os/learn.py)
- The extractor prompt asks for JSON `memories`, `updates`, `forget`, `entities`, `relations` (optional `replaces`), and `ended`. Rules: third person, user-said only, durability of about a month, preferences kept faithful, no entity for the user, relative dates converted using `Today is {weekday, YYYY-MM-DD}`, empty arrays when nothing durable. There is no explicit NONE event. The user text is clipped to 4000 characters and the assistant text to 3000. Model is `extractionModel` or the chat model. `extractionModel` defaults to `""`. — [`EXTRACT_PROMPT`, `learn_from_exchange`](backend/personal_os/learn.py), [`DEFAULT_SETTINGS`](backend/personal_os/llm.py)
- Candidate memories shown to the extractor are tagged `[M1]…`. If `MemoryIndex.query_vec` returns a vector, candidates are `index.candidates` (top 20 hybrid hits, then `Memories.for_context` filled to 40). Otherwise `Memories.for_context(..., limit=60)`. — [`learn_from_exchange`](backend/personal_os/learn.py), [`MemoryIndex.candidates`](backend/personal_os/memory_index.py)
- New memories: `Memories.create`, `source="auto"`, provenance `{conversation_id, message_id}` stored as `source_conversation_id` / `source_message_id`. Exact duplicate of a live row in the same scope (`lower(content)`) returns the existing row. Kinds accepted by the extractor: `fact|preference|goal|note`; anything else becomes `fact`. Content shorter than 6 characters is dropped. — [`learn_from_exchange`, `KINDS`](backend/personal_os/learn.py), [`Memories.create`](backend/personal_os/repos.py)
- Updates call `Memories.supersede` after a fresh read. The call is skipped if the row is gone, pinned, or its content changed since the snapshot. Supersede inserts a new live row, sets the old row’s `invalid_at` and `superseded_by`, and deletes the old FTS row. A pinned id passed into `supersede` itself would be rewritten in place and stay valid; the extractor never reaches that branch because of the pin skip. — [`learn_from_exchange`](backend/personal_os/learn.py), [`Memories.supersede`](backend/personal_os/repos.py)
- Extractor `forget` calls `Memories.invalidate`: sets `invalid_at`, drops FTS, refuses pinned and already-invalid rows. It does not hard-delete. — [`learn_from_exchange`](backend/personal_os/learn.py), [`Memories.invalidate`](backend/personal_os/repos.py)
- The model can also save during the turn with `save_memory` (`source="auto"`, no provenance). `personal: true` stores `project_id` NULL; otherwise the chat’s project. — [`save_memory`](backend/personal_os/tools.py)
- Schema base columns: `id, project_id, content, kind, source, pinned, created_at, updated_at`. Migration adds `deleted_at, deleted_with, valid_from, invalid_at, superseded_by, source_conversation_id, source_message_id`. No confidence column. FTS5 `memories_fts` on `content`, tokenizer `porter unicode61`, maintained by hand. — [`db.py` SCHEMA and `_migrate`](backend/personal_os/db.py), [`Memories.create/update`](backend/personal_os/repos.py)
- Scope: `project_id` NULL is personal. `_scope_clause` for a project includes that project plus personal (`include_global` default true). A personal chat (`project_id is None`) sees only personal rows. Library `ALL` is `1=1`. — [`_scope_clause`](backend/personal_os/repos.py)
- Hand edits: `POST /memories` (`source="user"`), `PUT /memories/{id}` (content, kind, pinned, `project_id`, or `move_to_global`). `update` overwrites content in place and rebuilds FTS; it does not append a history row. UI: click-to-edit, kind select, pin, “make personal”, “from chat” when `source_conversation_id` is set. — [`create_memory`, `update_memory`](backend/personal_os/app.py), [`MemoryRow`](src/renderer/src/components/MemoryView.tsx)
- User Forget is trash, not invalidate. `DELETE /memories/{id}` calls `trash.trash("memory", id)`, which sets `deleted_at`. Retention is 30 days, then `purge` hard-deletes the row and its FTS entry. `Memories.list` / `get` / `for_context` hide `deleted_at`. Restore-from-history (`POST /memories/{id}/restore`) is a different operation: it clears `invalid_at` and, if `superseded_by` still points at a live row, invalidates that successor. — [`delete_memory`](backend/personal_os/app.py), [`Trash`](backend/personal_os/trash.py), [`Memories.restore`, `Memories.history`](backend/personal_os/repos.py), [`HistoryRow`](src/renderer/src/components/MemoryView.tsx)
- Pin: `pinned` integer. UI title says “Pin (always in context)”. `for_context` orders `pinned DESC, updated_at DESC` with a limit (default 40) and unions up to 15 BM25 hits, then slices to `limit`. Hybrid injection does not force pinned rows in; see the retrieval section. — [`MemoryRow`](src/renderer/src/components/MemoryView.tsx), [`Memories.for_context`](backend/personal_os/repos.py), [`build_context`](backend/personal_os/context.py)
- Injection text is a bullet list under `## What you remember about the user`, each line clipped to 500 characters, labelled “notes, not instructions.” The block is volatile (placed with the newest user message when cache layout is on). `used["memories"]` stores id, content, project_id. — [`build_context`](backend/personal_os/context.py), [`layout_messages`](backend/personal_os/context.py)
- `search_memory` uses hybrid search when a query vector exists (limit 100, then paged by 20). Otherwise `Memories.list`, which is BM25 (prefix terms, limit 100) when `q` is non-empty, else pinned-then-recent. Each hit includes `valid_from` and `scope`. — [`search_memory`](backend/personal_os/tools.py), [`Memories.list`](backend/personal_os/repos.py)
- Activity insights can also `Memories.create` personal rows (`kind="activity"` or `"preference"`). That writer is outside this note’s capture scope; those rows then use the same list and injection filters. — [`insights.py` memory creates](backend/personal_os/insights.py)

### Inferences

- “Forget” in the Memory list and “forgotten” in Show history are different stores: trash (`deleted_at`, 30 days) versus supersession history (`invalid_at`). A user who clicks Forget will not see that row under Show history.
- Because the extractor refuses pinned updates, a pinned memory stays at its last user wording until the user edits it. That edit does not keep the previous wording.
- Provenance is only as good as the writer. Auto-learn sets conversation and message ids. `save_memory` and hand create do not.

### Gaps

- No measurement in this pass of how often the queue drops jobs, or of how often the model returns empty arrays.
- Whether `insights` memories are meant to be in the same injection budget was not traced past the `create` call.

## How does the knowledge graph work (extraction, edit, injection)?

### Takeaway

**Shipped.** The same extraction call upserts nodes and edges. Identity is exact label within a scope. Chats inject a 1-hop neighborhood of labels that appear in the user message, skipping edges with `invalid_at` set. The Memory graph view is a force layout with hand edit and hard delete. **Partial:** edges can be invalidated (`ended` / `replaces`) but the graph UI never shows that history and deletes are permanent. The `fact` text column is unused by extraction.

### Cited Findings

- Nodes: `kg_nodes(id, project_id, label, type, properties JSON, created_at, updated_at)`. Unique on `(IFNULL(project_id,''), lower(label))`. Types the extractor is told to use: `person|project|organization|tool|place|concept|other`. Labels equal to user/me/myself/i (case-insensitive) are dropped. — [`db.py`](backend/personal_os/db.py), [`learn_from_exchange`, `SELF_LABELS`](backend/personal_os/learn.py)
- `upsert_node` returns the existing node on exact label match in that scope and merges properties. It does not fuzzy-match. — [`Graph.upsert_node`, `Graph.find_node`](backend/personal_os/repos.py)
- Edges: base columns plus migrated `valid_at, invalid_at, superseded_by, source_message_id, fact` (default `''`). Unique on `(source_id, target_id, lower(relation))`. `upsert_edge` revives an invalidated duplicate instead of inserting a second row. Learn passes `source_message_id` and does not pass `fact`. — [`db.py` migration](backend/personal_os/db.py), [`Graph.upsert_edge`](backend/personal_os/repos.py), [`learn_from_exchange`](backend/personal_os/learn.py)
- `replaces` (`Source|relation|Target`) and `ended` look up a live edge and call `invalidate_edge` (sets `invalid_at`, optional `superseded_by`). Lookup does not create nodes. — [`_edge_by_ref`, `learn_from_exchange`](backend/personal_os/learn.py), [`Graph.invalidate_edge`](backend/personal_os/repos.py)
- `Graph.get` drops edges with `invalid_at` unless `include_invalid=true`. Nodes have no invalid flag. `GET /graph` exposes `include_invalid`. The renderer `api.graph.get` does not send it. — [`Graph.get`](backend/personal_os/repos.py), [`get_graph`](backend/personal_os/app.py), [`api.graph`](src/renderer/src/lib/api.ts)
- Injection: if `useGraph` (default true), `Graph.neighborhood` loads the whole in-scope graph, seeds nodes whose label is a substring of the query or shares a word (label length > 3 for the word test), then keeps 1-hop edges and caps nodes at 30. Prompt section `## Knowledge graph (relevant entities)` lists entities and `A —[relation]→ B` triples, marked notes not instructions. Empty seed set injects nothing. — [`Graph.neighborhood`](backend/personal_os/repos.py), [`build_context`](backend/personal_os/context.py)
- Tools: `graph_search` is neighborhood with `max_nodes=40`. `graph_traverse` walks up to depth 4 (default 2) from an exact or substring label, caps 80 entities and 120 relations, and loads the full graph in Python. `graph_add` upserts two nodes and an edge in the chat’s project scope and refuses user-self labels. No tool invalidates an edge. — [`graph_search`, `graph_traverse`, `graph_add`](backend/personal_os/tools.py)
- UI: `GraphView` force-directed graph; node panel edits label, type, and properties JSON, adds edges, and hard-deletes nodes and edges via `DELETE /graph/nodes/{id}` and `DELETE /graph/edges/{id}`. No history toggle. — [`GraphView`, `NodePanel`](src/renderer/src/components/GraphView.tsx), [graph routes](backend/personal_os/app.py)
- Hand routes: `POST/PUT/DELETE` for nodes and edges. `PUT` edge can change relation and properties, not validity. — [graph routes](backend/personal_os/app.py), [`Graph.update_node`, `Graph.update_edge`, `Graph.delete_node`, `Graph.delete_edge`](backend/personal_os/repos.py)

### Inferences

- “Postgres” and “PostgreSQL” stay two nodes until a consolidation proposal is applied. Extraction will not merge them.
- A contradicted relation can leave injection (invalid edges are filtered) while both nodes remain. The graph screen still looks like a hard-delete editor, so users cannot see or restore an invalidated edge there.
- Neighborhood and traverse scan every in-scope node and edge on each call. There is no separate graph index.

### Gaps

- No count of live nodes or edges in a real user database was taken.
- `fact` is stored and defaulted, and this pass did not find a writer that fills it from extraction or the graph UI.

## How is writing voice learned, when is it injected, and when is it explicitly not used?

### Takeaway

**Shipped.** Voice is a separate store from memories: verbatim samples and one profile per scope (personal or one project). Banking is automatic for prose chat turns and for doc saves; the profile is re-derived lazily. Injection is the profile markdown in the stable system prefix whenever the chat’s Writing style toggle is on and the profile is enabled and non-empty. **The “do not sound like the user when replying to them” rule is text in that block, not a second code path that omits the block.** Hand edits set `edited` and stop auto-relearn.

### Cited Findings

- Tables `style_samples` and `style_profiles` (one profile per scope via unique index on `IFNULL(project_id,'')`). Profile fields: summary, guidelines, traits, phrases, avoid, `enabled` (default 1), `edited`, sample_count, sample_chars, model. — [`style.py` SCHEMA](backend/personal_os/style.py)
- `looks_like_prose`: at least 220 characters, at least 2 sentences, at least 40 words, at least 82% letters or whitespace, no code fence, no markup tag, quoted lines at most 25%, at most two tokens longer than 24 characters. — [`looks_like_prose`, `MIN_SAMPLE_CHARS`](backend/personal_os/style.py)
- Chat banking: after the reply, if no error, turn not tainted, global `learnStyle` true (default true), chat `autoLearn` true, and `looks_like_prose(user_text)`. `learn_style_from_exchange` stores `source="chat"` (filter on) and may `relearn`. This runs inside the chat generator after `done` is yielded, not on `LearnWorker`. — [`_chat_stream` style hook](backend/personal_os/app.py), [`learn_style_from_exchange`](backend/personal_os/style.py)
- Doc save: if `learnStyle` and the doc has no meeting recording, `add_sample(..., source="doc", ref="doc:{id}")` with the default prose check. Same ref refreshes one row (`folded` reset to 0) instead of inserting another. Doc save does not call `relearn`. — [`save_doc`](backend/personal_os/app.py), [`WritingStyle.add_sample`](backend/personal_os/style.py)
- Hand paste and `POST /style/samples` use `check=False`. Tool `save_writing_sample` also uses `check=False`, default `personal=true`, `source="chat"`, and does not relearn. — [`add_sample` route](backend/personal_os/app.py), [`save_writing_sample`](backend/personal_os/tools.py)
- Cap: newest 80 samples per scope; older rows are deleted. Analysis reads newest 24 samples or 24,000 characters. Relearn when there is no profile yet, or at least 3 unfolded samples (`RELEARN_EVERY`), unless `edited` is set. `force=True` (`POST /style/learn`, UI “Re-read my writing”) ignores both. A parse with no summary and no guidelines leaves the old profile. A profile that becomes `edited` during the call is not overwritten unless `force`. Failed model output does not blank a profile (`relearn` returns None). — [`should_relearn`, `relearn`, constants](backend/personal_os/style.py)
- Analysis prompt asks for summary, up to 10 guidelines, up to 10 traits, up to 8 phrases, up to 8 avoid lines. Traits are model text, not a local stylometric measurement. The prompt says not to record what the samples are about. — [`ANALYSIS_PROMPT`, `clean_profile`](backend/personal_os/style.py)
- Injection: `useStyle` default true, and `style.for_context`. A project profile is used only when that profile is enabled and has a summary or guidelines; otherwise the personal profile. Profiles are not merged. `context_block` returns `""` if missing, disabled, or empty of summary, traits, guidelines, phrases, and avoid. The block is appended to the stable system prefix (not the per-turn volatile section). Header: `## How the user writes (their voice)`. Footer tells the model to use the voice only for text the user will send or publish, that it is not permission to send or delete, and not to imitate it when speaking to the user; a one-off tone instruction wins. — [`build_context`](backend/personal_os/context.py), [`WritingStyle.for_context`, `context_block`, `STYLE_FOOTER`](backend/personal_os/style.py)
- `writing_style` tool returns the same profile for drafting when the block is not already in context, with the same reply-vs-draft note. — [`writing_style`](backend/personal_os/tools.py)
- Not learned from: short or non-prose chat, tainted turns, `learnStyle` false, chat `autoLearn` false, docs that fail the prose filter or that contain a meeting recording. Not injected when: `useStyle` is false, no `WritingStyle` is passed, or `context_block` is empty (disabled or blank profile). Global `learnStyle` does not by itself stop injection of an existing profile. — [`_chat_stream`](backend/personal_os/app.py), [`build_context`](backend/personal_os/context.py), [`context_block`](backend/personal_os/style.py)
- UI control: Voice panel edits summary, guidelines, phrases, avoid; enable switch; reset (`DELETE /style`, optional samples); sample delete. Editing sets `edited` via `PUT /style`. Settings → “Learn how you write” is `learnStyle`. Per-chat toggle is “Writing style”. — [`StyleView`](src/renderer/src/components/StyleView.tsx), [`StylePatch`](backend/personal_os/app.py), [Settings memory tab](src/renderer/src/components/SettingsModal.tsx), [`ContextDrawer` toggles](src/renderer/src/components/ContextDrawer.tsx)
- Tainted chat: banking is skipped. `build_context` does not read the taint flag, so an existing profile is still injected if `useStyle` is on. The context drawer copy says auto-learn and the writing voice stay off until clear. — [`_chat_stream`](backend/personal_os/app.py), [`build_context`](backend/personal_os/context.py), [drawer taint copy](src/renderer/src/components/ContextDrawer.tsx)
- Samples are not injected as few-shot exemplars. One profile per scope, not per register (email vs chat vs doc). — [`context_block`](backend/personal_os/style.py)

### Inferences

- README’s “injected when drafting / not used for replies” matches the footer instruction. The implementation still sends the profile on every styled turn, including ordinary questions.
- A project can bank three long docs and still not refresh the profile until a qualifying chat message calls `relearn` or the user presses Re-read.
- Chat `autoLearn` off stops voice banking even when `learnStyle` is on. The settings copy presents those as separate switches.

### Gaps

- No eval in repo, found in this pass, that checks whether a draft matches the profile.
- `save_writing_sample` stores `source="chat"` even for a deliberate paste. Whether the Voice panel’s source tag is then wrong was not checked in the UI.

## What context toggles and inspector views exist for memory, graph, and auto-learn?

### Takeaway

**Shipped.** Per-chat toggles and a Context drawer with Last reply, Preview, and Trace. Memory panel has list, graph, split, and Voice, plus Show history and Tidy up. **Partial:** drawer hints still describe memory as pinned/recent/matching and do not mention hybrid rank or supersession. No settings field for the embedding model or `consolidateEvery`.

### Cited Findings

- New-chat defaults: `useMemory`, `useGraph`, `useDocuments`, `useActivity`, `useStyle`, `useMeetings`, `autoLearn`, `useTools` all true. `useSkills` is not in `DEFAULT_CONV_SETTINGS`; the drawer treats missing `useSkills` as on (`!== false`). — [`DEFAULT_CONV_SETTINGS`](backend/personal_os/repos.py), [`ContextDrawer`](src/renderer/src/components/ContextDrawer.tsx)
- Context drawer toggles that touch this scope: Memory (“Pinned, recent and matching memories”), Knowledge graph (“Entities mentioned + their neighbours”), Writing style, Auto-learn (“Extract memories, graph & writing style after each reply”, and the control is off when global `autoLearn` is off). Also Documents, Activity, Meetings, Tools, Skills. Scope line: “{project} + personal” or “Personal”. — [`ContextDrawer`](src/renderer/src/components/ContextDrawer.tsx)
- Inspector tabs: Last reply (latest message `context_used`), Preview (`POST /context/preview` for a draft, same `build_context` path including `_memory_hits`), Trace (waterfall spans, including a `learn` span on the worker and a `style` span when a prose message is banked). `ContextUsedView` lists memories (globe icon if personal, `stale` class if the id is not in the current memory list), graph triples, and writing style summary plus guidelines with an edit link. — [`ContextDrawer`, `ContextUsedView`](src/renderer/src/components/ContextDrawer.tsx), [`context_preview`](backend/personal_os/app.py)
- Global settings, Memory & learning tab: Auto-learn, Learn how you write, extraction model (blank = chat model). Knowledge base tab embeds `MemoryPanel`. No renderer reference to `embeddingModel`, `hybridRetrieval`, `retrievalMode`, or `consolidateEvery`. Those keys exist only in `DEFAULT_SETTINGS` (`embeddingModel` default `qwen3-embedding-8b`, `hybridRetrieval` true, `retrievalMode` `"hybrid"`, `consolidateEvery` 25). — [`SettingsModal`](src/renderer/src/components/SettingsModal.tsx), [`DEFAULT_SETTINGS`](backend/personal_os/llm.py)
- Memory panel modes: Split, List, Graph, Voice. Search box on list/graph. Copy under the list: personal memories go into every chat; project memories only into that project; pinned always included; the rest by recency and relevance. Show history loads `include_invalid=true` and renders `invalid_at` rows struck through with Restore. Tidy up calls `POST /memories/consolidate` and lists pending proposals with Apply and Dismiss. — [`MemoryPanel`](src/renderer/src/components/MemoryPanel.tsx), [`MemoryView`](src/renderer/src/components/MemoryView.tsx)
- `context_used` is stored on the assistant message and returned on `done`. — [`_chat_stream`](backend/personal_os/app.py), [README “How a reply is built”](README.md)

### Inferences

- Preview and Last reply can disagree with the drawer’s memory hint whenever hybrid retrieval is on, because the hint describes the fallback ranker.
- `consolidateEvery` and the embedding switch are live backend settings with no control in Settings, so a user cannot see or change them in the UI found here.

### Gaps

- Whether `PUT /settings` from somewhere else exposes `embeddingModel` was not found in `src/renderer`. Absence is “no match in ts/tsx”, not a proof that no other client sets it.

## Is retrieval BM25 only, hybrid, or embedding-backed? Any validity intervals, supersession, or consolidation?

### Takeaway

**Shipped hybrid, with a BM25 fallback.** When `embeddingModel` is set, `hybridRetrieval` is true, and `retrievalMode` is not `bm25`, memory retrieval fuses four lists with reciprocal rank fusion: FTS BM25, brute-force cosine, pinned/recent, and graph-label seeds. Embeddings are optional and time out after 2 seconds. **Shipped** validity and supersession on memories and edges, and approve-only consolidation. **Absent:** cosine clustering inside consolidation, model-chosen validity intervals, and a guarantee that pinned rows are in the injected set.

### Cited Findings

- `MemoryIndex.enabled`: needs a non-empty `embeddingModel`, `hybridRetrieval` not false, and `retrievalMode != "bm25"`. Default settings satisfy that (`qwen3-embedding-8b`, hybrid, hybridRetrieval true). `retrievalMode` is also the document-retrieval switch. — [`MemoryIndex.enabled`](backend/personal_os/memory_index.py), [`DEFAULT_SETTINGS`](backend/personal_os/llm.py)
- Vectors: table `memory_vectors(memory_id PK, model, dim, vec blob, content_hash, updated_at)`, cascade on memory delete. Stored float32, L2-normalised, via numpy. Embedder POSTs `{baseUrl}/v1/embeddings` in batches of 32, timeout 10s, backoff 300s after failure. `index` embeds up to 200 stale live rows per call (`invalid_at IS NULL` only). Content hash is SHA1 of stripped text. `POST /memories/reindex` resets backoff and indexes one batch. — [`memory_index.py`](backend/personal_os/memory_index.py), [`embed.py`](backend/personal_os/embed.py), [`reindex_memories`](backend/personal_os/app.py)
- Chat path: `_memory_hits` schedules a background backfill, awaits `query_vec` (query clipped to 2000 chars, 2s timeout). None means `build_context` uses `Memories.for_context` (pinned/recent limit 40 plus up to 15 BM25). A vector means `search(..., limit=40)` replaces that list entirely. — [`_memory_hits`](backend/personal_os/app.py), [`build_context`](backend/personal_os/context.py)
- `search` rankers, each depth 50: `_bm25` (`bm25(memories_fts)`, live rows only), `_cosine` (dot product, model match, dim match, scan cap 5000), `_recent` (`pinned DESC, updated_at DESC`, excludes deleted), `_graph_seeded` (neighborhood labels longer than 2 characters, substring of memory content). RRF in `embed.rrf`, k=60, weights BM25 1.0, cosine 1.0, recency 0.5, graph 0.7. Exceptions return `[]`. — [`MemoryIndex.search` and rankers](backend/personal_os/memory_index.py), [`rrf`](backend/personal_os/embed.py)
- BM25 and cosine SQL filter `invalid_at` and not `deleted_at`. `Memories.get` then drops deleted rows, so trash does not appear in results, but a trashed id can still take a rank slot before it is dropped. `for_context` and `Memories.list` filter both flags. — [`_bm25`, `_cosine`](backend/personal_os/memory_index.py), [`Memories.get`](backend/personal_os/repos.py)
- FTS query builder: tokens of length ≥3, max 12, OR of quoted terms. Prefix stars only for the search box (`Memories.list`), not for `for_context` or `_bm25`. — [`fts_query`](backend/personal_os/repos.py)
- Validity: live means `invalid_at IS NULL`. `valid_from` is set to `now()` on create and supersede. There is no separate world-time column the model fills, and no confidence. Edges have `valid_at` defaulting to `now()` and `invalid_at`. Default reads hide invalid memories and invalid edges. — [`Memories.create`, `Memories.list`, `Graph.get`, `Graph.upsert_edge`](backend/personal_os/repos.py)
- Supersession chain: `history()` walks `superseded_by` oldest-first. `GET /memories/{id}/history` and `POST /memories/{id}/restore`. Show history in the UI. — [`Memories.history`](backend/personal_os/repos.py), [`memory_history`, `restore_memory`](backend/personal_os/app.py), [`MemoryView`](src/renderer/src/components/MemoryView.tsx)
- Consolidation (**shipped, approve-only**): `memory_proposals` kinds `merge_memories | rewrite_memory | merge_entities`, status `pending|applied|dismissed|stale`. Candidates, cap 20: token Jaccard ≥ 0.6 within the same scope, unpinned; auto memories older than 30 days matching a relative-date regex; entity pairs of the same type that are alias-like (normalized equal, token subset, edit distance ≤ 1 for compact labels ≥ 6, or prefix for length ≥ 5). No cosine threshold. Model ids are remapped to `C*` / `N*`. `propose` never applies. `apply` for memories calls `supersede` on the first id and `invalidate` on the rest, and marks stale if content changed or a row was pinned. Entity apply re-points edges, drops self-loops and unique-index clashes, stores the loser label under `properties.aliases`, deletes the loser node. — [`consolidate.py`](backend/personal_os/consolidate.py)
- Auto proposals: `LearnWorker._maybe_consolidate` adds the count of newly created auto memories and calls `propose` every `consolidateEvery` (default 25; 0 disables). Publishes `proposals`. Manual path: Memory “Tidy up” → `POST /memories/consolidate`. Apply and dismiss: `POST /memories/proposals/{id}/apply|dismiss`. — [`LearnWorker._maybe_consolidate`](backend/personal_os/learn.py), [consolidate routes](backend/personal_os/app.py), [`MemoryView.tidy`](src/renderer/src/components/MemoryView.tsx)
- Pinned rows are excluded from candidate generation and rejected again in `_validate` and `_apply_memories`. — [`Consolidator.memory_candidates`, `_validate`, `_apply_memories`](backend/personal_os/consolidate.py)

### Inferences

- With the defaults, a working embedding route makes chat injection hybrid. README’s “pinned, then recent, plus full-text” describes only the fallback (`query_vec` is None, or hybrid switched off).
- Recency weight 0.5 means a pinned memory with no lexical, vector, or graph support can fall out of the top 40. The pin tooltip and the Memory panel sentence overclaim “always”.
- Consolidation does not use the vectors memory-2 stores. Near-duplicates that do not share tokens at Jaccard 0.6 are invisible to tidy-up.
- Relative dates are a prompt instruction plus a later rewrite proposal for auto rows older than 30 days. Nothing in `learn_from_exchange` checks that the model actually absolutized the date.

### Gaps

- No runtime confirmation in this pass that the default embedding model is actually routed. The default string is `qwen3-embedding-8b`; if the route is down, `Embedder` backs off 300s and retrieval falls back without raising.
- `memory_vectors` rows for a soft-deleted memory stay until purge (cascade is on hard delete). Effect on rank slots is inferred from the SQL, not measured.

## Where do README and docs/research/sota-memory.md disagree with the code?

### Takeaway

Both documents describe the pre-hybrid, overwrite-and-delete memory system. The working tree has implemented the note’s memory-1, memory-2, and memory-3 designs, with the deviations listed below. README’s reply pipeline omits voice-as-always-on prompt text, supersession, and hybrid rank.

### Cited Findings

`docs/research/sota-memory.md` section “Where we are” (the status claim, not the later spec) says:

- No provenance, no confidence, no validity interval, and no embeddings anywhere in the backend. Code has provenance columns, `valid_from` / `invalid_at` / `superseded_by`, `memory_vectors`, and `embed.py`. Confidence is still absent. — [sota-memory.md “Where we are”](docs/research/sota-memory.md), [`db.py` migration](backend/personal_os/db.py), [`memory_index.py`](backend/personal_os/memory_index.py)
- `updates` overwrite in place and `forget` hard-deletes unless pinned. Code supersedes and invalidates. Pinned rows are skipped by the extractor rather than rewritten. — [sota-memory.md](docs/research/sota-memory.md), [`learn_from_exchange`](backend/personal_os/learn.py)
- Read path is only `Memories.for_context` (pinned+recent up to 40, union 15 BM25). That remains the fallback. The chat path prefers `MemoryIndex.search` when a query vector exists. — [sota-memory.md](docs/research/sota-memory.md), [`_memory_hits`](backend/personal_os/app.py)
- “M1 provenance, M3 temporal, M4 sleep-time rewriter, M5 hybrid retrieval, M8 linking are NOT shipped.” M1/temporal supersession, hybrid retrieval, and approve-only consolidation are in the tree. Sleep-time auto-apply is still absent (`propose` never applies). Linking memories to each other (tags/neighbor rewrite) was not found. — [sota-memory.md “Adjacent, already shipped”](docs/research/sota-memory.md), [`learn.py`](backend/personal_os/learn.py), [`memory_index.py`](backend/personal_os/memory_index.py), [`consolidate.py`](backend/personal_os/consolidate.py)
- The same file’s memory-2 spec says: `llm.embed` via `litellm.aembedding`; vectors with stdlib `array('f')`; numpy not required; `rrf` lives in `memory_index.py`; `embeddingModel` default empty; a settings text field for the model. Code: HTTP embeddings in `embed.py` using numpy; `rrf` is `embed.rrf`; default `embeddingModel` is `qwen3-embedding-8b`; no settings field in the renderer. — [sota-memory.md memory-2](docs/research/sota-memory.md), [`embed.py`](backend/personal_os/embed.py), [`llm.py` defaults](backend/personal_os/llm.py)
- memory-2 spec: extractor candidates are top 20 hybrid union pinned/recent up to 40. Code matches that when a vector exists (`candidates`). Injection is top 40 hybrid only, not that union. — [sota-memory.md](docs/research/sota-memory.md), [`MemoryIndex.search` vs `candidates`](backend/personal_os/memory_index.py)
- memory-3 spec: duplicate clusters may also use cosine ≥ 0.88 when the index exists. `memory_candidates` uses Jaccard only. — [sota-memory.md memory-3](docs/research/sota-memory.md), [`Consolidator.memory_candidates`](backend/personal_os/consolidate.py)
- memory-1 spec: extractor may still supersede a pinned memory by rewriting it in place. `learn_from_exchange` continues past pinned updates before calling `supersede`. The in-place branch inside `supersede` is therefore unused by auto-learn. — [sota-memory.md memory-1](docs/research/sota-memory.md), [`learn_from_exchange`](backend/personal_os/learn.py), [`Memories.supersede`](backend/personal_os/repos.py)
- memory-1 spec: relation objects carry a natural-language `fact`. The column exists; the prompt and `upsert_edge` call from learn do not set it. — [sota-memory.md](docs/research/sota-memory.md), [`EXTRACT_PROMPT`](backend/personal_os/learn.py)

README disagreements:

- “How a reply is built” step 2: memories are pinned first, then recent, plus full-text matches. That is `for_context`, not the default hybrid path. Step 4’s “top BM25” for documents is outside this note; the memory sentence is in scope and is incomplete. — [README](README.md), [`_memory_hits`](backend/personal_os/app.py)
- Step 6: auto-learn is “a second (cheaper) model call” that extracts memories and graph relations, on a background worker after the run ends. The worker part matches memory extraction. Cheaper is true only when `extractionModel` is set; the default is empty, so the chat model is used. Voice relearn is a further call still inside the chat generator when a prose message is banked. The step does not mention supersede, invalidate, or consolidation. — [README](README.md), [`DEFAULT_SETTINGS`](backend/personal_os/llm.py), [`_chat_stream`](backend/personal_os/app.py)
- Memory feature blurb: edit, pin, move scope, forget. It does not mention Show history, restore, or Tidy up, which the UI has. — [README](README.md), [`MemoryView`](src/renderer/src/components/MemoryView.tsx)
- Voice blurb: injected when drafting something the user will send, and explicitly not used for replies to the user. The footer says that. The block is still inserted on every turn with Writing style on, including non-draft turns. — [README](README.md), [`build_context`](backend/personal_os/context.py), [`STYLE_FOOTER`](backend/personal_os/style.py)
- Context-management bullet lists toggles for memory, graph, documents, activity, meetings, auto-learn, and tools. The drawer also has Writing style and Skills. — [README](README.md), [`ContextDrawer`](src/renderer/src/components/ContextDrawer.tsx)
- `docs/writing-style.md` (README links it) says a doc save is banked, and that the prose filter is what keeps chat commands out. `save_doc` calls `add_sample` with the filter left on, so a short doc or one with a code fence is not banked. It also does not say that chat `autoLearn` false blocks banking. — [docs/writing-style.md](docs/writing-style.md), [`save_doc`](backend/personal_os/app.py), [`_chat_stream`](backend/personal_os/app.py)

What the research note still gets right, because the code did not add it:

- No per-memory confidence. No graph communities or node summaries. No exemplar injection of raw writing samples. No per-register voice. Entity resolution at extract time is still exact label. — [`db.py`](backend/personal_os/db.py), [`Graph.find_node`](backend/personal_os/repos.py), [`context_block`](backend/personal_os/style.py)

### Inferences

- Treat `sota-memory.md` “Where we are” and “NOT shipped” as a snapshot from before memory-1/2/3 landed in this tree. The spec sections in the same file are closer, but they are not a line-accurate description of `embed.py`, pin handling, or consolidation clustering.
- A gap comparison that starts from README step 2 will under-count retrieval (it will miss embeddings) and will miss that contradicted memories are retained as history.

### Gaps

- File mtimes and git history were not used to date the research note. The user said notes dated 2026-10-01 lose to code; the disagreement above is content versus the working tree, not a verified authorship date.
- `docs/research.md` (the index the sota note cites for M1–M8) was not re-read line by line. Only `sota-memory.md` and README were compared, plus `docs/writing-style.md` where README points at it.
