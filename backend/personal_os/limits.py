"""Every tuning number the app ships with, and why. Stdlib only: nothing here imports from personal_os.

There are no budgets: nothing caps a reply's rounds, tokens, cost or wall-clock time. A reply, a job, a subagent or a
desk turn ends when the model stops calling tools, or a stuck breaker fires (REPEAT_LIMIT, TOOL_ERROR_LIMIT, the stuck
detector, a denial streak), or the user stops it. The one exception is a turn of an alternative agent loop, which is also
held to LOOP_MODEL_CALLS. The only clocks are hang detection (LLM_IDLE_SECONDS for a silent provider stream,
FINAL_ROUND_SECONDS for the closing answer, SUBAGENT_STALE/TOOL_SECONDS for a child, JOB_IDLE_SECONDS for an unattended
run) and per-request timeouts on tools.

llm.DEFAULT_SETTINGS takes its numeric defaults from these constants. Four settings default to 0, meaning
"automatic": contextWindow (the model's window from the proxy, else CONTEXT_WINDOW_FALLBACK) and subagentMaxConcurrent /
deskMaxLive / parallelReads (worker_slots()). A stored non-zero value for any key is honoured as an override, and
PUT /settings validates every key in RANGES. Constants nobody tunes (retention ages, snapshot limits, retry counts, job
policy, shell and browser limits, subagent depth and hang clocks, the fetch cache) are read here directly, not from settings. Readers of the automatic keys go through
context_window and slots, never the raw setting.
"""
from __future__ import annotations

import functools
import os
from typing import Any

# ---- Context ----
CONTEXT_WINDOW_FALLBACK = 128_000  # used only when neither the proxy nor an overflow told us the real window
CONTEXT_WINDOW_FLOOR = 4096        # a learned or stored window never goes below this
COMPACT_AT = 0.7                   # summarize history past this share of the window (token counts are len//4 estimates)
COMPACT_AT_TOKENS = 30_000         # summarize once the replayed history (not the cached system prompt) passes this many estimated tokens
COMPACT_KEEP_TOKENS = 10_000       # newest history kept verbatim after a compaction
MICRO_AT_TOKENS = 64_000           # stub old tool results in a run once the whole request passes this
COMPACT_KEEP_RECENT = 8            # newest messages never summarized
MICRO_AT = 0.25                    # stub old tool results past this share of the window (time to first token dominates past ~30k)
MICRO_KEEP = 3                     # newest tool results left intact
MESSAGE_WINDOW_FRACTION = 0.5      # one message may fill at most this share of the window
MCP_DEFER_ABOVE = 12               # offer connector tools through search once more than this many are ready (0 = send all)
TOOL_DEFER_ABOVE = 40              # past this many built-in tools, send the core set plus tool_search (0 = send all)
# Share of the context window each injected block may take. They match the old fixed token counts (1500, 800, 2000,
# 3000, 1000, ~1500 for skills) at CONTEXT_WINDOW_FALLBACK to within 4%; the memory code reads them only through context_shares().
# "map" is the workspace map (context_map.py): about 900 tokens at the fallback window, its stable half riding in the cached prefix.
CONTEXT_SHARES = {"memories": .012, "graph": .006, "chunks": .016, "pinned": .023,
                  "profile": .008, "skills": .012, "map": .007,
                  "extension": .01}  # one extension's context block, unless it asks for another share
# Workspace map (context_map.py). Item caps for the prompt copy; the context_map tool pages through everything.
MAP_FILES = 12                     # notes and files listed in the prompt
MAP_PROJECTS = 8                   # projects listed in the prompt
MAP_UPLOADS = 5                    # newest uploads listed in the prompt
MAP_TOPICS = 8                     # memory topic labels listed in the prompt
MAP_TITLE_CHARS = 60               # a title is cut to this many characters
MAP_SHRUNK_TOKENS = 500            # the map's cap in worker prompts
MAP_HARD_CEILING_TOKENS = 1500     # whatever the window, the map never exceeds this
MAP_PAGE = 25                      # lines per context_map call
MAP_UPCOMING_EVENTS = 3            # calendar events in the volatile status block

# ---- Reply loop (stuck detection, no caps) ----
JOB_IDLE_SECONDS = 1800            # an unattended run with no model or tool activity this long is stopped (hang detection, not a length cap)
REPEAT_LIMIT = 5                   # identical tool calls in a row before a reply is stopped
TOOL_ERROR_LIMIT = 3               # consecutive errors from one tool before the model is told to change approach
FINAL_ROUND_SECONDS = 90.0         # a closing-answer model call still open after this long is abandoned
LOOP_MODEL_CALLS = 60              # model calls one turn of an alternative agent loop may make (the default loop has no cap)
STOP_GRACE_SECONDS = 3.0           # a write that Stop caught mid-call gets this long to report its real outcome before it is cancelled
REVIEW_TIMEOUT_SECONDS = 30        # a hung reviewer call fails closed (-> an approval card)

# ---- Provider ----
LLM_RETRIES = 3                    # retries before a reply's first token
LLM_IDLE_SECONDS = 300             # a stream silent this long is abandoned (a reasoning model can think a while)
TOOL_READ_RETRIES = 2              # extra attempts for a read-only tool after a transient network error

# ---- Agents ----
SUBAGENT_MAX_DEPTH = 2             # how deep subagents may nest
SUBAGENT_STALE_SECONDS = 450       # hang detection: a child with no model or tool activity this long is stopped
SUBAGENT_TOOL_SECONDS = 1200       # hang detection: a child stuck inside one tool this long is stopped
WORKFLOW_MAX_FAN_OUT = 50          # most items one fan-out step may map over
DESK_PARK_AFTER_SECONDS = 180      # a desk waits this long on a card nobody watches before letting the run go (0 = forever)
BROWSER_MAX_TABS = 4               # tabs per desk browser
BROWSER_IDLE_SECONDS = 300         # an idle agent browser is closed after this
SHELL_TIMEOUT_SECONDS = 120        # foreground shell default; a call may ask for up to 600
SHELL_MAX_BACKGROUND = 4           # live background shell jobs at once
CODING_SESSION_MAX_CONCURRENT = 3  # live coding sessions at once, their own pool apart from SHELL_MAX_BACKGROUND
LOGIN_SHELL_TIMEOUT_SECONDS = 5    # resolving the user's login-shell PATH for a new claude daemon
# Workers (workers.py): detached background agents the chat's front agent hands work to. These route and queue work;
# none of them ends a worker's run.
DELEGATION_AFTER_ROUNDS = 4        # rounds of tool calls a chat reply makes itself before the rest must go to a worker (routing, not a cap)
WORKER_MAX_CONCURRENT = 8          # workers running at once; the rest wait in a queue and start in order
WORKER_MEMORY_FLOOR = 0.15         # a queued worker is not started while free system memory is below this share
WORKER_RECHECK_SECONDS = 5.0       # how often a non-empty queue looks again for a free slot or recovered memory
RUN_HEARTBEAT_SECONDS = 20.0       # a working child's agent_runs.updated_at is bumped at most this often (liveness, never a limit)
WORKER_STALL_NOTE_SECONDS = 300    # a worker with no model, tool or job output this long says so in its status line
WORKER_STALL_NUDGE_SECONDS = 600   # ...and is sent one automatic message asking what it is blocked on (a worker waiting on a job or a session is not idle)
WORKER_STALL_STOP_SECONDS = 1200  # ...and, still silent, is stopped and the front agent is woken with a stall report (resumable)

# ---- Jobs ----
JOB_RETRY_BACKOFF_S = 120          # retry backoff base, doubles per attempt
JOB_FAILURE_STREAK_LIMIT = 3       # consecutive failed fires before a job is switched off
PROPOSAL_EXPIRE_DAYS = 7           # a pending proposal expires after this many days (0 = never)
JOB_EXPIRE_DAYS = 0                # a recurring job pauses this many days after arming (0 = never)
GMAIL_SEND_HOLD_SECONDS = 90       # undo window on outgoing mail (clamped to 60-120 on read)

# ---- Storage ----
MAX_UPLOAD_MB = 100                  # largest file POST /documents accepts (Files, the composer, drag-drop); mirrored in src/renderer/src/lib/uploadNote.ts
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_UNZIPPED_BYTES = MAX_UPLOAD_BYTES * 5 // 2  # a docx/xlsx's declared uncompressed size, summed; refused past this (zip bomb guard)
FILE_SNAPSHOT_MAX_BYTES = 5_000_000  # largest file pre-image kept
FILE_SNAPSHOT_RETAIN_DAYS = 14       # undo history age
FILE_SNAPSHOT_BUDGET_MB = 200        # disk budget for pre-images
SNAPSHOT_AFTER_MS = 120_000          # how long a closing run waits for its after-snapshot (run.after)
RETAIN_USAGE_DAYS = 365              # bookkeeping age: usage rows
RETAIN_TRACE_DAYS = 60               # bookkeeping age: traces
RETAIN_TOOL_RESULT_DAYS = 30         # bookkeeping age: stored tool results
RETAIN_APPROVAL_DAYS = 90            # bookkeeping age: decided approvals
FETCH_CACHE_SECONDS = 3600           # fetch_url reuses a page fetched this recently (0 = never)

# ---- Retrieval / misc ----
RETRIEVAL_MIN_SIMILARITY = 0.25    # drops vector-only hits below this similarity
RETRIEVAL_PER_DOC_CAP = 3          # passages per document
RETRIEVAL_CANDIDATES = 20          # candidates per ranker
CONSOLIDATE_EVERY = 25             # propose a memory tidy-up after this many new auto memories (0 = manual only)
TELEGRAM_LONG_RUN_MINUTES = 3      # when a run not started from Telegram counts as long

# Accepted ranges for PUT /settings (finite numbers only). The keys in AUTOMATIC also accept 0, meaning "derive it".
AUTOMATIC = ("autoArchiveDays", "contextWindow", "subagentMaxConcurrent", "deskMaxLive", "parallelReads")
RANGES: dict[str, tuple[float, float]] = {
    "uiZoom": (80, 160),
    "subagentMaxConcurrent": (1, 20),
    "llmRetries": (0, 10),
    "llmIdleSeconds": (10, 3_600),
    "autoArchiveDays": (1, 3_650),  # 0 = off
    "contextWindow": (1000, 4_000_000),
    "compactAt": (0.1, 0.95),
    "microAt": (0.05, 0.95),
    "compactKeepRecent": (2, 200),
    "compactAtTokens": (4_000, 4_000_000),
    "compactKeepTokens": (1_000, 1_000_000),
    "microKeep": (0, 50),
    "parallelReads": (1, 8),
    "retrievalMinSimilarity": (0, 1),
    "retrievalPerDocCap": (1, 10),
    "retrievalCandidates": (5, 50),
    "telegramLongRunMinutes": (1, 1440),
    "deskMaxLive": (1, 1000),
    "codingSessionMaxConcurrent": (1, 20),
    "delegationAfterRounds": (1, 20),
    "workerMaxConcurrent": (1, 16),
    "planAutoApproveSeconds": (0, 600),  # 0 = off
    "codeModeMaxCalls": (1, 500),
}


def _pos(v: Any) -> float:
    """`v` as a positive finite number, else 0 (bool, junk, negative and NaN all read as "not set")."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not v > 0 or v == float("inf"):
        return 0
    return v


def context_window(stored: Any = None, known: int | None = None, learned: int | None = None) -> int:
    """The window all context fractions are taken from: the proxy's figure, else the fallback; a stored override may
    only lower it (a model never has more room than the proxy reports); then no larger than a limit an overflow
    taught us; never below CONTEXT_WINDOW_FLOOR."""
    w = int(_pos(known) or CONTEXT_WINDOW_FALLBACK)
    if _pos(stored):
        w = int(min(_pos(stored), _pos(known))) if _pos(known) else int(_pos(stored))
    if learned and learned > 0:
        w = min(w, int(learned))
    return max(CONTEXT_WINDOW_FLOOR, w)


@functools.cache
def worker_slots() -> int:
    """How many things may run at once on this machine: half the cores or half the GB, whichever is fewer, 2 to 8."""
    cpu = os.cpu_count() or 2
    try:
        mem_gb = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2**30
    except (ValueError, OSError, AttributeError):
        mem_gb = 8
    return max(2, min(cpu // 2, int(mem_gb // 2), 8))


def slots(settings: dict[str, Any], key: str) -> int:
    """A concurrency setting: the stored value when above 0, else worker_slots()."""
    return int(_pos(settings.get(key))) or worker_slots()


def share_tokens(window: int, ratio: float) -> int:
    """Tokens `ratio` of a `window`-token context comes to, with the window capped at CONTEXT_WINDOW_FALLBACK."""
    return int(min(window, CONTEXT_WINDOW_FALLBACK) * ratio)


def context_shares(window: int) -> dict[str, int]:
    """Tokens each injected context block may take in a `window`-token context. A window past CONTEXT_WINDOW_FALLBACK
    does not buy bigger blocks: they are re-sent (mostly uncached) every turn."""
    return {k: share_tokens(window, r) for k, r in CONTEXT_SHARES.items()}

# Heading of the per-turn retrieval system message (context.layout_messages); llm.py needs it without importing context.
VOLATILE_HEADER = "## Context for this turn"
