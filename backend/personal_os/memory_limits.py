"""Every tuning number of long-term memory (repos.Memories, memory_index.py, learn.py, context.py), and why.

Stdlib only: nothing here imports from personal_os. The prompt sections' share of the context window lives in
limits.CONTEXT_SHARES with the other retrieval blocks; this module holds what is specific to memory.
"""
from __future__ import annotations

# ---- Retrieval (memory_index.py) ----
RANK_DEPTH = 50          # how deep each ranker reads; well past the few lines a turn's window share can carry
VECTOR_CAP = 5000        # rows the brute-force cosine ranker scans (a numpy dot over 5k short rows stays under ~10 ms)
QUERY_TIMEOUT = 2.0      # seconds a chat turn waits for the query embedding before going lexical-only
INDEX_BATCH = 200        # memories embedded per index() call, so a backfill never holds the route for long
QUERY_CHARS = 2000       # characters of the query that are embedded; the head of a message carries its topic
# RRF weights. A lexical or semantic match is the evidence; graph seeding is weaker (a label hit, not the row's
# meaning); recency only orders rows that already matched and never brings one in by itself.
W_BM25 = 1.0
W_COSINE = 1.0
W_GRAPH = 0.7
W_RECENT = 0.5
# A vector-only hit counts as relevant at this cosine or above. Higher than the documents floor
# (limits.RETRIEVAL_MIN_SIMILARITY, 0.25): a passage is one of several cited excerpts, but a memory line is injected
# with no reranker every turn it matches, and a short unrelated sentence routinely scores near that floor.
MEMORY_MIN_SIMILARITY = 0.4
LEXICAL_HITS = 15        # keyword hits per lexical pass (FTS, then CJK substring); a few strong matches beat many weak ones
CONTEXT_HITS = 40        # most log rows a turn asks retrieval for; the "memories" window share trims further
SEARCH_HITS = 100        # most rows search_memory ranks before paging
SEARCH_PAGE = 20         # rows per search_memory page

# ---- Write-time dedupe ----
# A new memory this close (cosine) to a live, unpinned row in scope supersedes it instead of adding a row: the same
# statement reworded scores ~0.95+, a different fact about the same subject well under 0.9 (calibrated with fixed
# vectors in tests/test_memory_tiers.py). A changed value ("150 words" -> "300 words") that clears it is still right
# to supersede: newer wins.
NEAR_DUP_SIMILARITY = 0.92

# ---- Tainted-chat save_memory ----
# In a chat that read untrusted text, save_memory skips its card only when at least this share of the memory's
# content words appear in one message the user typed in this chat. The rest is the third-person rewrite ("User", a date).
TAINT_SAVE_MIN_OVERLAP = 0.6

# ---- Extraction (learn.py) ----
EXTRACT_USER_CHARS = 4000       # the user's message as the extractor sees it; memories come from the user's words
EXTRACT_ASSISTANT_CHARS = 3000  # the reply is context for what the user meant, so it gets less room
EXTRACT_TOOL_CHARS = 2000       # one-line tool call summaries, for friction the prose does not show
EXTRACT_EXISTING = 60           # existing memories shown for reconciliation when there are no embeddings
CANDIDATES = 40                 # existing memories shown when embeddings rank them
CANDIDATES_TOP = 20             # of those, how many come from the hybrid ranking (the rest: pinned, then recent)
EXISTING_LINE_CHARS = 2000      # one existing memory line in the extractor prompt
MIN_MEMORY_CHARS = 6            # shorter extracted text is noise ("ok", "yes")

# ---- Tidy-up and provenance (learn.py, migrations.py) ----
# Settings-table row holding the epoch seconds of the last consolidation proposal run: the tidy-up
# counter is derived from rows newer than this, so it survives a relaunch.
TIDY_AT_KEY = "memoryTidyAt"
# How long after an assistant reply finished an auto-learn write can land (extraction is one LLM call
# queued behind the reply); a wider window would start guessing which reply a memory came from.
BACKFILL_WINDOW_S = 180
