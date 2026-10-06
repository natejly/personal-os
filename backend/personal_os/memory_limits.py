"""Every tuning number of long-term memory (repos.Memories, memory_index.py, learn.py, context.py), and why.

Stdlib only: nothing here imports from personal_os. Token budgets for the prompt sections live in
limits.CONTEXT_BUDGET with the other retrieval blocks; this module holds what is specific to memory.
"""
from __future__ import annotations

# ---- Retrieval (memory_index.py) ----
RANK_DEPTH = 50          # how deep each ranker reads; well past the few lines a turn's budget can carry
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
CONTEXT_HITS = 40        # most log rows a turn asks retrieval for; the "memories" token budget trims further
SEARCH_HITS = 100        # most rows search_memory ranks before paging
SEARCH_PAGE = 20         # rows per search_memory page

# ---- Write-time dedupe ----
# A new memory this close (cosine) to a live, unpinned row in scope supersedes it instead of adding a row: the same
# statement reworded scores ~0.95+, a different fact about the same subject well under 0.9 (calibrated with fixed
# vectors in tests/test_memory_tiers.py). A changed value ("150 words" -> "300 words") that clears it is still right
# to supersede: newer wins.
NEAR_DUP_SIMILARITY = 0.92

# ---- Profile (always-on standing preferences) ----
# The profile rides in the cached system prefix every turn, so it may never take more than this share of the
# model's context window, whatever the contextBudget "profile" setting says.
PROFILE_WINDOW_SHARE = 0.02

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

# ---- Graph extraction ----
GRAPH_MIN_CONFIDENCE = 0.6          # an extracted edge the model rates below this is dropped (guesses read as facts later)
GRAPH_PROMPT_CANDIDATES = 40        # existing entities shown to the extractor so it reuses them instead of making near-duplicates
GRAPH_USER_TEXT_CHARS = 4000        # user text the extractor reads (the same cut the memory extractor uses)
GRAPH_ASSISTANT_TEXT_CHARS = 1500   # the reply is context only (nothing is extracted from it), so less of it is enough
GRAPH_RESOLVE_SIMILARITY = 0.85     # embedding cosine above which a new name is the same entity as an existing one
GRAPH_PROMPT_EDGES = 60             # known relations shown to the extractor; more is context it cannot use and tokens it pays for
GRAPH_MAX_TRIPLES = 12              # per exchange; more than this is the model transcribing, not remembering
GRAPH_DEFAULT_CONFIDENCE = 0.8      # a triple with no usable confidence: kept (the model did not hedge) but under the 0.9 it gives for outright statements
GRAPH_NAME_CHARS = 200              # an entity name or edge endpoint shown or stored; a longer one is pasted text, not a name
GRAPH_ALIAS_CHARS = 80              # a nickname or abbreviation; short by nature, so a long one is not one
GRAPH_RELATION_CHARS = 80           # a stored relation phrase (legacy rows may hold a free-form one) in a prompt or a context line
GRAPH_LABEL_CHARS = 80              # a related_to label is a short phrase; longer is the model quoting the sentence

# ---- Graph retrieval ----
GRAPH_MATCH_SIMILARITY = 0.55       # message-to-entity cosine that seeds retrieval without a literal mention
GRAPH_MIN_MENTION_CHARS = 2         # shortest label or alias matched as a whole word in the message ("PG")
GRAPH_CONTEXT_MAX_SEEDS = 8         # entities a message may seed; past this the block is a graph dump, not context
GRAPH_CONTEXT_MAX_EDGES = 24        # live 1-hop edges considered before the token budget trims
GRAPH_RECENCY_HALF_LIFE_DAYS = 90   # an edge's recency weight halves every this many days since it became true
GRAPH_QUALIFIER_CHARS = 120         # the role or relationship note shown after an edge line; longer is a sentence, not a qualifier
GRAPH_NODE_VECTOR_CAP = 5000        # node vectors scanned by the brute-force cosine matcher

# ---- Graph backfill ----
GRAPH_BACKFILL_DELAY_SECONDS = 1.0  # pause between extraction calls, so a backfill never crowds out live chat on the proxy
GRAPH_INDEX_BATCH = 200             # nodes embedded per index() call: one embedding request burst, not one per node
GRAPH_BACKFILL_PROGRESS_SECONDS = 5  # how often the command-line backfill prints its counts
GRAPH_BACKFILL_BATCH = 200          # user messages read per database page
