"""Every tuning number the app ships with, and why. Stdlib only: nothing here imports from personal_os.

llm.DEFAULT_SETTINGS takes its numeric defaults from these constants. Five settings default to 0, meaning
"automatic": contextWindow (the model's window from the proxy, else CONTEXT_WINDOW_FALLBACK), maxToolRounds
(stuck detection ends loops; MAX_ROUNDS_HARD is the backstop), and subagentMaxConcurrent / deskMaxLive /
parallelReads (worker_slots()). A stored non-zero value for any key is honoured as an override, and PUT /settings
still accepts and validates every key in RANGES, so older stored values keep loading. Readers of the five
automatic keys go through context_window, max_rounds and slots, never the raw setting.
"""
from __future__ import annotations

import functools
import os
from typing import Any

# ---- Context ----
CONTEXT_WINDOW_FALLBACK = 128_000  # used only when neither the proxy nor an overflow told us the real window
CONTEXT_WINDOW_FLOOR = 4096        # a learned or stored window never goes below this
COMPACT_AT = 0.7                   # summarize history past this share of the window (token counts are len//4 estimates)
COMPACT_KEEP_RECENT = 8            # newest messages never summarized
MICRO_AT = 0.25                    # stub old tool results past this share of the window (time to first token dominates past ~30k)
MICRO_KEEP = 3                     # newest tool results left intact
MESSAGE_WINDOW_FRACTION = 0.5      # one message may fill at most this share of the window
MCP_DEFER_ABOVE = 12               # offer connector tools through search once more than this many are ready (0 = send all)
TOOL_DEFER_ABOVE = 40              # past this many built-in tools, send the core set plus tool_search (0 = send all)
SKILLS_INLINE_BUDGET = 6000        # characters of approved skill text inlined in the system prompt
CONTEXT_BUDGET = {"memories": 1500, "graph": 800, "chunks": 2000, "activity": 800, "meetings": 800, "pinned": 3000}  # tokens per retrieval block

# ---- Run budget ----
RUN_TOKENS = 200_000               # per reply, prompt+completion summed over every model call (0 = none)
RUN_SECONDS = 300                  # per reply wall clock, approvals excluded (0 = none)
MAX_ROUNDS_HARD = 100              # backstop for one reply; stuck detection and REPEAT_LIMIT end loops long before this
JOB_MAX_ROUNDS = 8                 # unattended runs: model/tool rounds
JOB_RUN_TOKENS = 60_000            # unattended runs: tokens
JOB_RUN_SECONDS = 240              # unattended runs: wall clock
JOB_HARD_SECONDS = 1800.0          # an unattended run is abandoned after this, a backstop for a hang the budget cannot see
REPEAT_LIMIT = 5                   # identical tool calls in a row before a reply is stopped
TOOL_ERROR_LIMIT = 3               # consecutive errors from one tool before the model is told to change approach
FINAL_ROUND_SECONDS = 90.0         # a closing-answer model call still open after this long is abandoned
REVIEW_TIMEOUT_SECONDS = 30        # a hung reviewer call fails closed (-> an approval card)

# ---- Provider ----
LLM_RETRIES = 3                    # retries before a reply's first token
LLM_IDLE_SECONDS = 300             # a stream silent this long is abandoned (a reasoning model can think a while)
TOOL_READ_RETRIES = 2              # extra attempts for a read-only tool after a transient network error

# ---- Agents ----
SUBAGENT_MAX_DEPTH = 2             # how deep subagents may nest
SUBAGENT_MAX_ROUNDS = 12           # each child's round cap (its cost is also charged to the parent)
SUBAGENT_STALE_SECONDS = 450       # a child with no model or tool activity this long is stopped
SUBAGENT_TOOL_SECONDS = 1200       # a child stuck inside one tool this long is stopped
WORKFLOW_MAX_FAN_OUT = 50          # most items one fan-out step may map over
DESK_MAX_TURNS = 12                # turns a desk runs unattended before it stops and asks
DESK_PARK_AFTER_SECONDS = 180      # a desk waits this long on a card nobody watches before letting the run go (0 = forever)
BROWSER_MAX_TABS = 4               # tabs per desk browser
BROWSER_IDLE_SECONDS = 300         # an idle agent browser is closed after this
SHELL_TIMEOUT_SECONDS = 120        # foreground shell default; a call may ask for up to 600
SHELL_MAX_BACKGROUND = 4           # live background shell jobs at once
CODING_SESSION_TIMEOUT_MINUTES = 30  # an OpenCode coding session is stopped after this
CODING_SESSION_MAX_CONCURRENT = 3  # live coding sessions at once, their own pool apart from SHELL_MAX_BACKGROUND
LOGIN_SHELL_TIMEOUT_SECONDS = 5    # resolving the user's login-shell PATH for a new claude daemon

# ---- Jobs ----
JOB_RETRY_BACKOFF_S = 120          # retry backoff base, doubles per attempt
JOB_FAILURE_STREAK_LIMIT = 3       # consecutive failed fires before a job is switched off
PROPOSAL_EXPIRE_DAYS = 7           # a pending proposal expires after this many days (0 = never)
JOB_EXPIRE_DAYS = 0                # a recurring job pauses this many days after arming (0 = never)
GMAIL_SEND_HOLD_SECONDS = 90       # undo window on outgoing mail (clamped to 60-120 on read)

# ---- Storage ----
MAX_UPLOAD_MB = 50                   # largest file POST /documents accepts (Files, the composer, drag-drop); mirrored in src/shared/uploads.ts
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_UNZIPPED_BYTES = MAX_UPLOAD_BYTES * 5 // 2  # a docx/xlsx's declared uncompressed size, summed; refused past this (zip bomb guard)
FILE_SNAPSHOT_MAX_BYTES = 5_000_000  # largest file pre-image kept
FILE_SNAPSHOT_RETAIN_DAYS = 14       # undo history age
FILE_SNAPSHOT_BUDGET_MB = 200        # disk budget for pre-images
RETAIN_USAGE_DAYS = 365              # bookkeeping age: usage rows
RETAIN_TRACE_DAYS = 60               # bookkeeping age: traces
RETAIN_TOOL_RESULT_DAYS = 30         # bookkeeping age: stored tool results
RETAIN_APPROVAL_DAYS = 90            # bookkeeping age: decided approvals
SANDBOX_KEEP_DAYS = 14               # a stopped sandbox is deleted after this many idle days
FETCH_CACHE_SECONDS = 3600           # fetch_url reuses a page fetched this recently (0 = never)

# ---- Retrieval / misc ----
RETRIEVAL_MIN_SIMILARITY = 0.25    # drops vector-only hits below this similarity
RETRIEVAL_PER_DOC_CAP = 3          # passages per document
RETRIEVAL_CANDIDATES = 20          # candidates per ranker
CONSOLIDATE_EVERY = 25             # propose a memory tidy-up after this many new auto memories (0 = manual only)
VOICE_LOOP_MAX_TURNS = 20          # cap on the hands-free voice loop
IMESSAGE_LONG_RUN_MINUTES = 3      # when a text-started run counts as long

# Accepted ranges for PUT /settings (finite numbers only). The keys in AUTOMATIC also accept 0, meaning "derive it".
AUTOMATIC = ("contextWindow", "maxToolRounds", "subagentMaxConcurrent", "deskMaxLive", "parallelReads")
RANGES: dict[str, tuple[float, float]] = {
    "maxToolRounds": (1, 60),
    "uiZoom": (80, 160),
    "maxRunTokens": (0, 10_000_000),
    "maxRunSeconds": (0, 86_400),
    "subagentMaxConcurrent": (1, 20),
    "subagentMaxDepth": (0, 3),
    "subagentMaxRounds": (1, 60),
    "subagentStaleSeconds": (0, 86_400),
    "subagentToolSeconds": (0, 86_400),
    "fileSnapshotMaxBytes": (0, 100_000_000),
    "fileSnapshotRetainDays": (1, 365),
    "fileSnapshotBudgetMB": (1, 20_000),
    "llmRetries": (0, 10),
    "llmIdleSeconds": (10, 3_600),
    "retainUsageDays": (7, 3_650),
    "contextWindow": (1000, 4_000_000),
    "compactAt": (0.1, 0.95),
    "microAt": (0.05, 0.95),
    "compactKeepRecent": (2, 200),
    "microKeep": (0, 50),
    "retainTraceDays": (1, 3_650),
    "retainToolResultDays": (1, 3_650),
    "retainApprovalDays": (1, 3_650),
    "toolReadRetries": (0, 5),
    "parallelReads": (1, 8),
    "browserMaxTabs": (1, 12),
    "browserIdleSeconds": (30, 86_400),
    "sandboxKeepDays": (0, 3_650),
    "retrievalMinSimilarity": (0, 1),
    "retrievalPerDocCap": (1, 10),
    "retrievalCandidates": (5, 50),
    "fetchCacheSeconds": (0, 86_400),
    "imessageLongRunMinutes": (1, 1440),
    "deskMaxLive": (1, 1000),
    "codingSessionTimeoutMinutes": (1, 1440),
    "codingSessionMaxConcurrent": (1, 20),
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


def max_rounds(settings: dict[str, Any]) -> int:
    """Rounds one reply may take: a stored maxToolRounds below MAX_ROUNDS_HARD, else MAX_ROUNDS_HARD."""
    n = int(_pos(settings.get("maxToolRounds")))
    return n if 0 < n < MAX_ROUNDS_HARD else MAX_ROUNDS_HARD
