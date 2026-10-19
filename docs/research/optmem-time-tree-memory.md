# Taelin's agent memory (OptMem) and a tree extension for Grain

Research date 2026-10-19. Grain sources are in /home/kyle/grain-worktrees/extensions.

## 1. Headline finding

The system is **OptMem**, repo https://github.com/VictorTaelin/OptMem (created 2026-07-25, last commit seen 2026-07-31, about 2.2k stars per the GitHub API). One correction to the brief: its tree is **chronological and binary, not topical**. Nobody decides where a fact goes. Memory i is leaf i, and every aligned power-of-two range of leaves has one summary line. This is time-based compression, not a hierarchy of subjects. I found no Taelin source describing a topical tree. If the brief meant a topical tree, I could not find it; treat that as unverified.

Verified by reading the repo README and the `memo` source (https://raw.githubusercontent.com/VictorTaelin/OptMem/main/memo, https://raw.githubusercontent.com/VictorTaelin/OptMem/main/README.md).

## 2. Timeline of sources (newest last)

X returned HTTP 402 to WebFetch, so the post texts below come from search-result titles and snippets and are unverified beyond that.

1. 2026-07-26, X post, https://x.com/VictorTaelin/status/2081453432318132603. Snippet: "every memory solution I tried sucks, so I rolled my own it is a plug and play prompt that gives any agent infinite memory db is an append-only log. compression on the spot (nap, don't sleep). nothing is ever deleted, memory context is constant-sized, details fade with age". A second search summary says he followed up with a command to expand compressed memories and said "nothing here is novel". I could not verify that wording.
2. 2026-07-25 to 07-31, GitHub repo history: first commit, "the memory context halves: 96 lines, ~8k tokens, 2 parts" (07-27), Windows lock fix from a contributor, and a "bytes, not characters" fix (07-29) (https://api.github.com/repos/VictorTaelin/OptMem/commits). The README prompt is the whole integration.
3. Secondary coverage: https://digg.com/tech/8d2l5808 (returned 404 to me, so unverified) and https://hills-lab.hr/field-notes/optmem-ai-agent-memory-review/ (a third-party review, not fetched in full).
4. Later X posts, snippet-only:
   - https://x.com/VictorTaelin/status/2105182389961908544: "a harness that replaces the chat by optmem. i.e., it IS the chat history, rather than an active tool. now I can forever talk to a single unified manager agent, which knows all my life, and has no tools other than spawn".
   - https://x.com/VictorTaelin/status/2106569004454219875: on "OptChat" ("the best upgrade to my workflow since GPT 3"; he exported all past chats into it).
   - https://x.com/VictorTaelin/status/2106575078838178270: "THE RECIPE ... Ask your agent to build this".
   - https://x.com/VictorTaelin/status/2108200878553588091: "My harness ... closest to infinite context I've experienced ... OSS next week, prompt to build your own below". Whether the release happened is unverified.
   - Dates for 3 and 4 are inferred from status-id ordering and the search summary (September to October 2026); not verified.

## 3. What he built

**Data structure.** Two stores under `~/.optmem/memory` (or `$MEMORY_DIR`):
- `LOG.txt`: every memory, one line each, append-only, never edited. Fixed-width records of 320 bytes, so position is identity and a lookup is one seek.
- `TREE/<size>`: one file per level (sizes 2, 4, 8 and so on). Each file holds fixed-width 288-byte summary records of one block, a "cache, rebuildable from the log alone".
- A node is a block [lo, hi), an aligned power-of-two range. Its summary is a one-line compression of its two halves. Depth is log2(N). A leaf is one memory of at most 280 bytes, stored with the date it was written.
- Source: https://github.com/VictorTaelin/OptMem/blob/main/README.md and the comments in `memo`.

**Writing.**
- The agent calls `memo note "<1 line, max 280 bytes>"` mid-work. The prompt says "Do not register redundant memories", so dedup is the agent's judgment. There is no code-level dedup.
- Placement is mechanical: append to the log.
- The `note` output (and `wake`) carries the next due compression. `memo nap <lo>-<hi> "<line>"` stores it. The same agent answers it: "Compress memories #a-b into one line of at most 280 bytes. Keep what has lasting effect, drop what does not. Invent nothing."
- Blocks of up to 16 memories are compressed from raw leaves. Larger blocks are compressed from their two child summaries. Blocks are built in order, smallest first.
- Maintenance is **inline and lazy, in the agent's own conversation**, one merge per tool output. The README says "Nothing ever runs in the background", and the tagline is "nap, don't sleep".
- There is no split. Merging is the only operation, and a node is created when both halves exist.

**Reading.**
- `memo wake` runs first in every session. It prints a "cover": a set of blocks that tiles the log, at most `WAKE_LINES` (default 96, about 8k tokens, paged into 2 parts for harness truncation limits).
- The cover keeps a block whole if its size is at most alpha times its age (`_cover`). Alpha is binary-searched to fit the line budget, and spare lines are spent splitting the blocks nearest the present. Recent memories are verbatim and old ones are coarse. If the log has no more entries than the budget, nothing is compressed.
- `memo zoom a-b` opens a node into its two halves. `memo recall <regex>` is a literal regex scan of the log.
- The agent navigates by choosing which node to zoom. There is no relevance scoring, no embeddings, and no retrieval model.
- If a needed summary has not been built yet, `wake` refuses and hands over the pending nap first.

**Consistency.**
- Nothing is deleted or rewritten, and there are no contradiction checks. A newer memory simply appears later, and the summaries can reconcile them when they are rewritten.
- `memo forget a-b` drops a bad summary, and the next nap rebuilds it.
- Subagents must not run `memo`: "it cannot judge what is already known, and its notes would arrive duplicated and incorrectly".
- Parallel sessions are all "you" and share one lock-protected store.

**Format.** Plain text lines in fixed-width files, no markdown or JSON. Install is `curl | sh`, which prints a `## Memory` block of 426 tokens for AGENTS.md or CLAUDE.md. The tool is one Python file with no dependencies.

**Numbers and claims (his own).**
- 426-token prompt.
- 96 lines, about 8k tokens per wake.
- "At a million memories (608 MB), `wake` takes 0.03s". This is a speed claim; there is no recall-quality benchmark.
- "memory context is constant-sized, details fade with age".
- I found no stated comparison with vector RAG or a knowledge graph beyond "every memory solution I tried sucks". The problems he solves are infinite history in a bounded context, no infrastructure, no lock-in ("outlives every session, compaction, model and vendor change"), and no lossy deletion.

## 4. Grain's current model

Flat facts live in `memories` (id, project_id with NULL meaning personal, content, kind, source user|auto, pinned, timestamps; /home/kyle/grain-worktrees/extensions/backend/personal_os/db.py). Kinds are fact, preference, instruction, goal and note (per docs/research/tencentdb-agent-memory.md section 1). Supersede chains (`superseded_by`, invalidation, restore, trash) keep history, and `Memories.create` dedups on exact lowercase content inside one IMMEDIATE transaction (backend/personal_os/repos.py `Memories`).

A graph sits beside it: `kg_nodes` and `kg_edges` (typed, with unique label and edge constraints).

Retrieval (`memory_index.py`, `memory_limits.py`) is hybrid. It fuses BM25, embedding cosine, graph-seed, temporal-window and recency rankers with weighted RRF, gates by a relevance floor (cosine 0.4), optionally reranks, and expands the query. A row is returned only if it matched.

`context.build_context` injects two blocks:
- A stable "Standing preferences" profile of pinned, preference and instruction rows. The About-you line is cut at 1500 characters.
- A volatile "Recalled memories" list of up to 40 hits, fitted to the memories share of the window, with the note "N more omitted; search_memory finds them".

Writes happen in `learn.learn_from_exchange`, which runs on a serial `LearnWorker` queue after each reply. One extraction call sees the 60 most relevant memories and returns memories, updates, forgets, entities and relations. A write-time judge (`judge_new`) and a cosine guard follow. `consolidate.py` makes approve-only tidy-up proposals, and the About-you summary is drafted every 25 auto memories.

`working.py` is not long-term memory. It holds the plan artifact and tool-result handles.

Prior research in the repo: sota-memory.md, parity-memory.md, memory-extraction.md, hindsight-memory.md, tencentdb-agent-memory.md. The hindsight and TDAM notes already compared scene and consolidation designs, and none of the five covers OptMem.

Extension hooks (docs/extension-hooks.md):
- `turn.settled` (emit, after the reply; slow work goes to a worker)
- `context.contribute` (returns a Block, fitted to the extension's share, with a timeout)
- `memory.written`, `memory.deleted` (emit_sync, with a copy of the row)
- `memory.before_write` (can rewrite or deny)
- `RetrievalSources` (context.py:463): async fn that takes `{query, vec, project_id, conv_settings}` and returns `[{id, text, ref}]`
- A scoped DB via `db:own` and `data.tables` (extensions/scopeddb.py), and a per-extension data folder.

## 5. Design sketch: "time-tree" built-in extension

**Honest fit.** OptMem's tree answers a question Grain already half answers: what happened over a long history, within a fixed budget. Grain's retrieval answers "what do I know that relates to this query". A chronological tree gives Grain something it lacks, which is a bounded, always-current digest of the whole history where old detail fades. It does not replace relevance retrieval. For topical recall, Taelin's only mechanism is `recall <regex>`, which is weaker than Grain's.

**Where it lives.** Two choices:
- **Own tables via `db:own`** (recommended): `tree_leaf(seq, memory_id, text, ts)` and `tree_node(level, k, summary, built_from_hash, updated_at)`. Leaves reference `memories.id`, so memory rows stay the source of truth and the tree is a rebuildable cache, which is Taelin's stance. A hash of the child summaries lets a changed child mark its ancestors stale.
- Markdown or text files in the extension's data folder: easier to read and grep, but they lose transactionality and the join to memory ids.

**Write path.**
- `memory.written` appends a leaf (project-scoped, with personal as the NULL scope) and marks nodes pending. `memory.deleted` or a supersede tombstones the leaf, and the ancestors' summaries are rebuilt on the next pass.
- `turn.settled` pays down the pending merges on the worker, in the background, one or two per turn. Grain does not need Taelin's "agent does it inline" trick, because a cheap model can run the nap prompt. Keep his rule "Keep what has lasting effect, drop what does not. Invent nothing", and run output through `redact` as the other learn paths do.
- Filing new facts into the tree is not an LLM decision; they are appended. If a topical layer is wanted later, that is a separate decision (see questions).

**Read path.**
- `context.contribute` returns one volatile "History digest" block: the `cover` of the log, finest near now, fitted to a token budget (start with a fixed line count like WAKE_LINES, about 1k to 2k tokens, well below Taelin's 8k).
- Pinned and standing preferences stay in the existing profile.
- A `memory_zoom(node)` tool lets the model open a node into its two halves.
- A retrieval source could return the top nodes whose summaries match the query, with `ref` pointing at the node. Navigation would be BM25 or cosine over node summaries, then a descent to the best child, capped at a node count and a token budget per turn.
- Per-chat `useMemory` still gates it.

**Coexistence.**
- Keep the flat memory list, graph and hybrid retrieval. The tree is additive, a third view over the same rows.
- The digest could replace the "N more omitted" tail with something that preserves the gist of what was cut.
- Replacement of flat memories is not proposed: Grain's supersession, pinning and judge guards are stronger than append-only.

**First milestone.**
1. Tree tables plus a backfill from live `memories`, ordered by `valid_from` or `created_at`.
2. A `memory.written` and `memory.deleted` sync, with a pending-merge worker on `turn.settled`.
3. A `context.contribute` digest behind a default-off toggle with a fixed token budget.
4. A debug view showing the cover.
5. Measure: tokens per turn, digest staleness, and whether answers about older events improve on the existing temporal eval harness. No recall benchmark exists for OptMem to compare against.

**Open questions.**
- Does a chronological digest earn its tokens when Grain already ranks by relevance and a temporal window? The eval harness should decide.
- Summaries of superseded or retracted facts: rebuild ancestors on every supersede, or only when the node is next shown? The first costs LLM calls on each correction.
- Scope: one tree per project plus personal, or a single tree with scope tags. Taelin's is one stream.
- Privacy: summaries can leak facts across the project boundary or from an untrusted (tainted) turn.
- Which model runs the merge, and what a cost cap looks like. Taelin's design needs about N-1 merges for N memories.
- Does the model's zoom get used? Taelin's evidence is anecdotal ("closest to infinite context I've experienced").
- A topical tree (the original brief's premise) needs a placement and rebalancing rule that OptMem lacks. It would be a separate design, and the TDAM scene files (docs/research/tencentdb-agent-memory.md) are the nearest prior art in the repo, which that note recommended against adopting.
