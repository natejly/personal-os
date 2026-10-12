# Hindsight agent memory: what it does now and what Grain should take

Research note, 2026-10-11. Sources: the paper (arXiv 2512.12818v1, Dec 2025) and the repository at commit a9eeed77 (v0.10.3, 2026-10-10, MIT). This folder is the one place in the repo that names other products. Grain claims were spot-checked against `backend/personal_os/`.

## 1. Summary

Hindsight is an open-source memory service for agents (Postgres plus pgvector, FastAPI, MCP tools). It exposes three operations: retain (extract facts from text), recall (multi-strategy retrieval) and reflect (answer a question using memory). The paper describes four stores: world facts, experiences, entity-summary observations and opinions with confidence scores that move on reinforcement or contradiction.

The current repo has dropped that design. The opinion network and its confidence scores were removed by migrations in January and April 2026, and per-entity summaries are gone (the regenerate endpoint returns 410). In their place are observations: LLM-consolidated beliefs, each carrying a proof count, the ids of the facts it was built from, and a change history. Fact types are now `world`, `experience` and `observation`. Reflect became a tool-calling agent. User-defined standing questions ("mental models") are refreshed in the background when in-scope memories change.

The headline benchmark numbers come from the paper's pipeline, not the shipped code, and several baselines are copied from other vendors' reports. Our own docs mis-attribute 94.6% to the paper (section 3).

Bottom line for Grain: its storage, supersession, entity resolution and lexical retrieval are already at or past Hindsight's. The ideas worth taking are small and additive: a separate event time, evidence-backed observations delivered as reviewable consolidation proposals, and possibly user-pinned standing questions. Opinion confidence, the Postgres stack and unreviewed belief rewriting should be left alone.

## 2. How Hindsight works now

### Data model

- `memory_units`: text, embedding (default 384 dims), `fact_type`, context, metadata, tags, document and chunk ids, `occurred_start`/`occurred_end` (event time, null for ongoing facts) and `mentioned_at` (statement time). Observation rows add `proof_count`, `source_memory_ids`, `consolidated_at` and `observation_history` (capped at 50).
- `entities` (canonical name, first/last seen, mention count), `unit_entities`, `entity_cooccurrences`, and `memory_links` (temporal, semantic, causes/caused_by/enables/prevents; weight 0-1). Entity-to-entity edges were dropped in May 2026 in favour of a query-time join through `unit_entities`.
- A bank is an isolated store with a disposition (three soft traits, skepticism/literalism/empathy, each 1-5), hard "directives", and per-bank missions for retain, reflect and observations. Tags scope visibility, and observation dedup only happens within a tag scope.
- `experience` versus `world` is decided by speaker: the agent's own actions are experiences.

### Retain

- Text is chunked at 3000 characters (turn boundaries for conversation JSON). Modes: concise (default), verbose, custom, verbatim, chunks.
- The concise prompt asks for significant facts covering what/when/where/who/why, absolute dates, coreference resolution ("Emily (user's roommate)"), and a "would this be useful in 6 months" test. A coarse date spans its whole period (2015 becomes 2015-01-01..2015-12-31).
- Each fact has `fact_kind` event|conversation, event start/end, entities and at most two causal links to earlier facts in the same batch. Stored text is "what | When | Involving | why".
- The embedding input is augmented with "(happened in <date>)" and "[entities]"; the stored text is not.
- Entity resolution is lexical only: `pg_trgm` candidates (similarity 0.3, max 200), scored 0.5 name similarity + 0.3 co-occurrence + 0.2 temporal proximity (7 days), merged at 0.6. The docs admit short names get absorbed.
- Links: temporal weight `max(0.3, 1 - hours/24)` within the same fact type (top 20); semantic ANN top 50 at similarity >= 0.7; causal links from the LLM.
- No fact-level dedup at retain. Duplicates are left to observation consolidation. Re-submitting a document id replaces it; append mode diffs by chunk hash.

### Recall

Four arms run per fact type:

1. Semantic: HNSW, minimum similarity 0.3.
2. Keyword: Postgres `ts_rank_cd` over at most 16 selective terms. The docs say this is not true BM25.
3. Graph: seeds are the top 20 semantic hits; one hop over shared entities and semantic/causal links; score `tanh(0.5 * shared entities)` + semantic weight + causal weight + 1, range 0-3.
4. Temporal: only when the query has a time expression. Entry points are ANN hits overlapping the window (10, picked round-robin over 8 time buckets), then spread over temporal/causal links with relation boosts (causes 2.0, enables 1.5), decay 0.7, cutoff 0.2.

Fusion is RRF with k=60 and equal weights. A cross-encoder (ms-marco-MiniLM-L-6-v2) reranks the top 300. Final score is `CE * recency * temporal * proof_count`, each factor `1 + alpha * (signal - 0.5)`: recency alpha 0.2 (`clamp(1 - days/365, 0.1, 1)`), temporal 0.2, proof count 0.1 (signal `0.5 + ln(n)/10`). Coarse dates are scored from the end of the period.

Budget levels low/mid/high take 100/300/1000 candidates. Output is capped at 4096 tokens by default. Packing is greedy and skips an oversized item and continues; the top result is always returned. Docs quote recall at 100-600 ms.

### Reflect

Reflect is an agent loop (max 10 iterations, temperature 0.9) with tools `search_mental_models`, `read_mental_models`, `search_observations`, `recall`, `expand` (memory to chunk to document) and `done`. The prompt sets a hierarchy of mental models over observations over raw facts, and says to verify stale observations against raw facts. It must gather evidence before finishing, citations must be ids it actually retrieved, and it may only use retrieved information. The conflict protocol is: list the facts oldest to newest, the latest `mentioned_at` wins, say so when ambiguous. Output is text plus `based_on`. Docs quote 0.8-3 s.

### Observation consolidation

A background job (`consolidator.py`, about 3.6k lines) takes batches of 8 new facts per LLM call at temperature 0. For each batch it recalls related observations (tag-strict, with their source facts) and the LLM returns `{creates, updates, deletes}`, each with a reason. Rules: prefer update over create, one observation per facet, cascade updates, preserve history, deletes are rare, and no arithmetic on counts. Near-duplicates at similarity >= 0.97 go to an LLM merge-or-keep step. Deleting source facts deletes the observations derived from them. The consolidation recall interleaves arms round-robin instead of RRF, because RRF and the cross-encoder buried near-duplicate twins.

### Mental models

User-defined standing questions whose answers are stored and refreshed in the background, only when memories in scope have changed, using surgical delta edits. They are organised as a folder tree of pages.

### Paper versus repo drift

| Area | Paper | Repo now |
|---|---|---|
| Opinions | Opinion network, confidence, reinforcement of +/-alpha, -2alpha on contradiction | Removed (Jan and Apr 2026 migrations drop `confidence_score` and the type) |
| Entity summaries | Per-entity summary observations | Removed; observations are bottom-up beliefs with proof count, sources, history |
| Fact types | World, experience, observation, opinion | World, experience, observation |
| Disposition | Big-Five traits plus a bias parameter | Three soft traits plus hard directives |
| Entity edges | Entity links as graph edges | Dropped; query-time join via `unit_entities` |
| Temporal parsing | flan-t5 | `dateparser` by default |
| Causal links | Several types | Only `caused_by` written at retain |
| Reflect | Single generation call (CARA) | Tool-calling agent over mental models, observations, facts |
| Names | TEMPR (retain/recall), CARA (reflect) | Not used in the repo docs |

## 3. Benchmarks and how much to trust them

| Benchmark | System | Score | Source |
|---|---|---|---|
| LongMemEval-S | Full context, OSS-20B | 39.0 | Paper |
| LongMemEval-S | Full context, GPT-4o | 60.2 | Paper |
| LongMemEval-S | Zep | 71.2 | Paper (copied from Supermemory's technical report, GPT-4o judge) |
| LongMemEval-S | Supermemory | 81.6 / 84.6 / 85.2 | Paper (copied) |
| LongMemEval-S | Hindsight, OSS-20B / OSS-120B / Gemini-3 answerer | 83.6 / 89.0 / 91.4 | Paper |
| LongMemEval-S | Hindsight | 94.6 | Later [vendor leaderboard](https://benchmarks.hindsight.vectorize.io/); backbone and variance unstated |
| LoCoMo | Backboard | 90.0 | Self-reported, not reproducible per the paper |
| LoCoMo | Memobase / Zep | 75.78 / 75.14 | Paper (copied) |
| LoCoMo | Mem0 / Mem0 graph | 66.88 / 68.44 | Paper (copied from Mem0's report) |
| LoCoMo | LangMem / OpenAI | 58.1 / 52.9 | Paper (copied) |
| LoCoMo | Hindsight (three backbones) | 83.18 / 85.67 / 89.61 | Paper |
| LoCoMo | Hindsight | 92 | Later vendor leaderboard |
| BEAM 10M | Hindsight vs Honcho | 64.1 vs 40.6 | Vendor leaderboard |

Correction to our own docs: `roadmap.md` and `sota-memory.md` said the paper reports "94.6% on LongMemEval vs Zep 71.2% and Mem0 67.6%". That mixes three sources. 94.6 is the later vendor leaderboard figure, 71.2 is a Zep LongMemEval-S baseline that the paper copied from Supermemory's technical report (GPT-4o judge), not a number Zep reported itself or the paper re-ran, and Mem0's figure in the paper is a LoCoMo score (66.88, or 68.44 with graph), not LongMemEval. The paper's own LongMemEval-S result is 83.6 to 91.4 depending on the backbone.

Caveats:

- Baselines are copied from other vendors' reports, which used different judges and prompts.
- The judge is gpt-oss-120b. The paper's recall token budget is literally a `<add>` placeholder.
- The "independent reproduction" is by co-authors (Virginia Tech, Washington Post).
- The vendor itself says LoCoMo and LongMemEval no longer separate memory systems from long-context stuffing.
- The scores came from the paper's pipeline (with opinions and entity summaries), not the current code, so they do not describe what the repo does today.
- Unverified: the paper's embedding model, dollar cost per document, and the leaderboard backbones.

Treat the numbers as evidence that structured retrieval beats full context on these sets, nothing finer.

## 4. Side by side with Grain

| Mechanism | Hindsight | Grain today |
|---|---|---|
| Storage | Postgres, pgvector HNSW, per-bank partial indexes | SQLite; `memories` + FTS5 (`memories_fts`) + `memory_vectors` blobs (float32, re-embedded on content hash or model change); `kg_nodes`/`kg_edges` |
| Extraction | Per 3000-char chunk, LLM, concise/verbose modes | One call after each finished reply (`learn.py`), user text 4000 chars, reply 3000, low effort; sees top 20 hybrid candidates plus pinned plus recent as `[M1..]`; outputs adds, updates, forgets |
| Event time vs statement time | `occurred_start/end` and `mentioned_at`; coarse dates span the period | Only `valid_from` = learn time (`memory_index._temporal` docstring says so). Relative dates are rewritten to absolute inside the text by `normalize_memory`, but there is no structured event field |
| Dedup | None at retain; observations absorb duplicates; 0.97 merge step | At write: embedding near-dup at cosine >= 0.92 supersedes (`NEAR_DUP_SIMILARITY`), else exact-text dedup; later: Jaccard >= 0.6 candidates in the consolidation pass |
| Entity resolution | Trigram + co-occurrence + time, merge at 0.6, no LLM or embedding | Label/alias match, then embedding cosine >= 0.85 (`GRAPH_RESOLVE_SIMILARITY`); alias-like entity merge proposals reviewed by the user |
| Graph | Free-form entities, links by time/semantic/causal; one-hop expansion at query time | Closed vocabulary of 8 node types and 15 predicates, confidence floor 0.6, at most 12 triples per exchange; single-valued predicates supersede siblings; edges carry `valid_at`/`invalid_at`/`superseded_by` |
| Retrieval arms | Semantic, keyword (`ts_rank_cd`), graph, temporal | BM25 (true FTS5), cosine (brute-force numpy, floor 0.4), graph-seeded, recency, temporal window |
| Fusion | RRF k=60, equal weights | Weighted RRF: BM25 1.0, cosine 1.0, graph 0.7, recency 0.5, temporal 0.8 (`memory_limits.py`) |
| Rerank | Cross-encoder on top 300 | LiteLLM reranker on top 30, 1.5 s timeout with backoff, falls back to fused order |
| Score boosts | Recency, temporal, proof count (alpha 0.2/0.2/0.1) | Temporal boost (`TEMPORAL_BOOST` 1.6 scaled by window relevance) after fusion; no proof count or importance |
| Budgets | 100/300/1000 candidates; 4096 output tokens; greedy packing that skips oversized items | Context shares of the window (profile 0.008, hits 0.012, graph 0.006); search limit. `context._fit` keeps the longest relevance-ordered prefix that fits and stops at the first overflowing line (it pops from the tail, never skips) |
| Consolidation | Per 8 facts, creates/updates/deletes of observations, automatic | `consolidate.py`: deterministic candidates, medium-tier LLM proposes merge/rewrite/merge_entities, the user applies; auto-triggered after 25 new auto memories |
| Reflect / synthesis | Agent loop over mental models, observations, facts | None as a separate operation; the main agent calls `search_memory`, `graph_search`, `graph_traverse` |
| Provenance | `source_memory_ids`, `based_on`, document/chunk ids | `source_conversation_id`/`source_message_id` per memory, quote view in the UI; no derived-from links because nothing is derived |
| Supersession | Observation history (cap 50); facts are not versioned | Soft supersession on memories and edges (`invalid_at`, `superseded_by`), history view with restore, UI delete goes to trash |
| Scoping | Banks and tags | `project_id` (NULL = personal), project `isolated` mode, per-chat `useMemory`/`useGraph`/`autoLearn` |

Where Grain is already at or past Hindsight:

- Soft supersession with history and restore, on both memories and graph edges. Hindsight keeps no per-fact version trail.
- User-reviewed consolidation proposals. Hindsight rewrites beliefs unreviewed.
- Entity resolution that uses embeddings and aliases, against a purely lexical matcher.
- True BM25 through FTS5, where Hindsight's keyword arm is not BM25 by its own admission.
- A closed-vocabulary graph with single-valued supersession, which avoids duplicate entity and predicate drift.
- Weighted RRF with a dedicated temporal ranker and a post-fusion temporal nudge, against equal-weight fusion.
- Sensitive-content gate, tainted-chat handling and per-memory expiry, which Hindsight has no equivalent for.

Where Hindsight is ahead: structured event time, synthesized beliefs with evidence counts, standing questions, and a dedicated reflect operation.

## 5. What to take

Ranked by value for effort. Each one keeps Grain's rule that the user reviews rewrites of what the assistant believes.

### 1. Event time separate from statement time (effort S-M)

Why: `valid_from` is the learn time, so "I moved to Berlin in 2019" learned today is indexed as today. The temporal ranker (`memory_index._temporal`, `temporal_query.relevance`) already exists and would immediately become more accurate for any query naming a period.

Mapping:
- Add nullable `occurred_start`/`occurred_end` to `memories` (`db.py` migration list near `valid_from`; `migrations.py`).
- Have the extractor emit them in the `memories` and `updates` objects in `EXTRACT_PROMPT` (`learn.py`), with coarse dates spanning the whole period.
- In `_temporal` and the post-fusion nudge, use `COALESCE(occurred_start, valid_from, created_at)`, and score period-only dates from the period end.
- Append "(happened in <date>)" to the text sent to the embedder only.

Risks: extractor error on vague dates (leave null rather than guess); one more field in the prompt output; existing rows have no value, which is fine because the fallback is today's behaviour.

### 2. Evidence-backed observations through the consolidation proposals (effort M)

Why: this is the part of Hindsight that survived its own redesign. Beliefs that cite the memory ids behind them can be audited and refreshed; unsupported confidence numbers were abandoned.

Mapping:
- Extend `consolidate.py` with a fourth proposal type, `synthesize_observation`, alongside `merge_memories`, `rewrite_memory` and `merge_entities` in `memory_proposals`.
- Store applied observations as `memories` with a new kind or flag, a `source_memory_ids` JSON column, `proof_count` (computed as the number of live sources, not asked of the LLM), and the existing supersession columns for history.
- Reuse Hindsight's consolidation rules in the prompt: prefer updating an existing observation to creating one, one per facet, no arithmetic.
- Small ranking boost at the end of `search` for proof count (their form is `1 + 0.1 * (0.5 + ln(n)/10 - 0.5)`, effectively under 10 percent).
- When a source memory is superseded or trashed, mark the observation `stale` through the existing proposal status.

Risks: LLM drift in rewritten beliefs (mitigated by review and by keeping sources); observations double-count their own sources in retrieval, so suppress a source when its observation is already in the result set; added review load for the user, so cap proposals per pass.

This also changes how to read sota-memory gap #7 (typed stores with confidence). Hindsight removed opinion confidence because confidence numbers moved by reinforcement and contradiction were not defensible. Grain should not build gap #7 as written. The evidence-backed observation covers the useful part, and the graph edges already carry an extraction confidence.

### 3. Standing questions (effort M)

Why: a user-pinned question ("What is the state of the Berlin move?") whose stored answer refreshes only when in-scope memories changed. It could replace part of the stable profile block or feed `meeting_brief` with a precomputed section.

Mapping: a small table (question, scope, answer, source ids, last input hash); a refresh step at the end of the learn job that compares the hash of in-scope memory ids and update times, and regenerates through the existing medium-tier model only on change; injection through `context.build_context` under a new share, or through a tool.

Risks: staleness bugs if the hash misses a change; token cost grows with question count (cap at a handful); overlaps the pinned/profile block, so it needs a clear rule for which wins. Build after item 2, since answers should cite observations.

### 4. Reflect as an agent hierarchy (effort S, mostly already covered)

Hindsight's reflect is a dedicated loop. Grain's main agent already calls `search_memory` (query, kind, since, include_chats), `graph_search` and `graph_traverse`, and has a "newer wins" instruction in the recall block, so most of the benefit exists. What it lacks is a rule to prefer synthesized beliefs over raw facts and to cite only retrieved ids. Once item 2 exists, add that wording to the `search_memory` tool description and the recall block; do not build a separate reflect operation.

### 5. Small retrieval tweaks (effort S each)

- Greedy packing that skips an oversized item and continues instead of stopping: worth doing. Grain does stop at the first overflow: `_fit` (`backend/personal_os/context.py:49`, called from `build_context` at :296, :323, :337, :374, :388) drops items from the tail until the joined block fits, so one long note ranked early blocks every lower-ranked note. Memory lines are clipped to 500 characters (`_one_line`), which bounds but does not remove the effect.
- Coarse dates scored from the period end: comes free with item 1.
- Experience versus world split (the agent's own actions): low value for Grain, whose memories are about the user. Skip unless agent self-notes become a use case.

### What not to take

| Item | Reason |
|---|---|
| Postgres/pgvector stack, banks, async worker | Grain is a single-user local app on SQLite; brute-force cosine is fine at this size |
| Lexical-only entity resolution | Grain's alias plus embedding resolution with user-reviewed merges is stronger; Hindsight's own docs list short-name absorption |
| Unreviewed automatic rewriting of beliefs | Conflicts with Grain's stance that the user approves changes to what it believes; Hindsight observations can drift |
| Per-chunk extraction | Grain extracts once per reply with candidates for reconciliation; chunking adds cost for no gain on chat input |
| Consolidation every 8 facts | Write-path LLM cost is unmeasured in Hindsight and open issues show scaling trouble (OOM at 36K facts in delta retain, #3848) |
| 23k-line engine | Complexity not justified; the useful rules fit in one prompt |
| Opinion confidence | Removed upstream; see item 2 |
| Equal-weight RRF, non-BM25 keyword arm | Grain's weighted fusion and FTS5 are better |

## 6. Sources

- Paper: https://arxiv.org/abs/2512.12818 (PDF https://arxiv.org/pdf/2512.12818)
- Repository: https://github.com/vectorize-io/hindsight at commit a9eeed77 (v0.10.3). Files read, relative to `hindsight-api-slim/hindsight_api/` unless noted: `engine/memory_engine.py`, `consolidator.py`, the retain, recall and reflect modules, and the Alembic migrations `i4d5e6f7g8h9_delete_opinions.py`, `g2h3i4j5k6l7_remove_opinion_fact_type.py` and `e9b2c7d1f3a4` (entity-link removal).
- Vendor leaderboard (LongMemEval-S 94.6, LoCoMo 92, BEAM 10M): the vendor's published benchmarks page, https://benchmarks.hindsight.vectorize.io/; backbones not stated.
- Open issues cited: #5488, #5485, #5473, #5474, #5476, #5448, #5497, #3848.
- Zep baseline: https://arxiv.org/abs/2501.13956. Mem0 baseline: https://arxiv.org/abs/2504.19413.
- Grain files checked: `backend/personal_os/memory_index.py`, `memory_limits.py`, `learn.py`, `consolidate.py`, `db.py`, `migrations.py`, `tools.py`.
