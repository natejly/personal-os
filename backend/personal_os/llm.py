"""Provider-agnostic LLM access over any OpenAI-compatible API (a LiteLLM proxy is just one of them)."""
from __future__ import annotations

import asyncio
import contextlib
import email.utils
import json
import logging
import random
import re
import time
import uuid
from contextvars import ContextVar
from typing import Any, AsyncIterator, Callable

import httpx

from . import auth_breaker, providers
from .limits import (BROWSER_IDLE_SECONDS, CODING_SESSION_MAX_CONCURRENT, BROWSER_MAX_TABS, COMPACT_AT, COMPACT_AT_TOKENS, COMPACT_KEEP_RECENT, COMPACT_KEEP_TOKENS, MICRO_AT_TOKENS, CONSOLIDATE_EVERY, DELEGATION_AFTER_ROUNDS, DESK_PARK_AFTER_SECONDS, FETCH_CACHE_SECONDS, FILE_SNAPSHOT_BUDGET_MB, FILE_SNAPSHOT_MAX_BYTES, FILE_SNAPSHOT_RETAIN_DAYS, GMAIL_SEND_HOLD_SECONDS, TELEGRAM_LONG_RUN_MINUTES, JOB_EXPIRE_DAYS, JOB_FAILURE_STREAK_LIMIT, JOB_RETRY_BACKOFF_S, LLM_IDLE_SECONDS, LLM_RETRIES, MCP_DEFER_ABOVE, MICRO_AT, MICRO_KEEP, PROPOSAL_EXPIRE_DAYS, RETAIN_APPROVAL_DAYS, RETAIN_TOOL_RESULT_DAYS, RETAIN_TRACE_DAYS, RETAIN_USAGE_DAYS, RETRIEVAL_CANDIDATES, RETRIEVAL_MIN_SIMILARITY, RETRIEVAL_PER_DOC_CAP, SANDBOX_KEEP_DAYS, SHELL_MAX_BACKGROUND, SHELL_TIMEOUT_SECONDS, SUBAGENT_MAX_DEPTH, SUBAGENT_STALE_SECONDS, SUBAGENT_TOOL_SECONDS, TOOL_DEFER_ABOVE, TOOL_READ_RETRIES, WORKER_MAX_CONCURRENT, WORKFLOW_MAX_FAN_OUT)
from .permissions import DEFAULTS as PERMISSION_DEFAULTS
log = logging.getLogger("personal_os.llm")

# Usage accounting. The app registers a listener; callers that know the chat/project set usage_context.
UsageListener = Callable[[dict[str, Any]], None]
_usage_listeners: list[UsageListener] = []
usage_context: ContextVar[dict[str, Any]] = ContextVar("usage_context", default={})
# Absolute time.monotonic() by which the current stream must be over; set only around a closing-answer call (a hang
# bound, not a reply cap). A context var rather than a parameter so every caller of stream_chat keeps its signature.
stream_deadline: ContextVar[float | None] = ContextVar("stream_deadline", default=None)
# Requests that share a prompt prefix (one chat's turns, sibling workers of one kind) carry the same id, so Fireworks
# routes them to the replica that holds that prefix in its cache. Set by the chat loop and the worker loop.
session_affinity: ContextVar[str | None] = ContextVar("session_affinity", default=None)


def on_usage(fn: UsageListener) -> None:
    _usage_listeners.append(fn)


def parse_usage(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Provider usage object -> the counts we keep, with cache and reasoning buckets normalised.

    OpenAI/Fireworks/LiteLLM report prompt_tokens_details.cached_tokens; Anthropic (through LiteLLM) adds
    cache_read_input_tokens / cache_creation_input_tokens. Reasoning tokens are already inside completion_tokens.
    """
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {k: raw[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens") if raw.get(k) is not None}

    def num(v: Any) -> int:
        try:
            return max(0, int(v or 0))
        except (TypeError, ValueError):
            return 0

    ptd = raw.get("prompt_tokens_details") if isinstance(raw.get("prompt_tokens_details"), dict) else {}
    ctd = raw.get("completion_tokens_details") if isinstance(raw.get("completion_tokens_details"), dict) else {}
    out["cached_tokens"] = num(ptd.get("cached_tokens")) or num(raw.get("cache_read_input_tokens"))
    out["cache_write_tokens"] = num(raw.get("cache_creation_input_tokens")) or num(ptd.get("cache_creation_tokens"))
    out["reasoning_tokens"] = num(ctd.get("reasoning_tokens"))
    return out


def _emit_usage(model: str, kind: str, usage: dict[str, Any] | None, duration_ms: int, prompt_chars: int, completion_chars: int) -> None:
    est = not usage or usage.get("prompt_tokens") is None
    rec = {
        "model": model, "kind": kind, "duration_ms": duration_ms, "estimated": est,
        "prompt_tokens": int((usage or {}).get("prompt_tokens") or prompt_chars // 4),
        "completion_tokens": int((usage or {}).get("completion_tokens") or completion_chars // 4),
        "cached_tokens": 0 if est else int((usage or {}).get("cached_tokens") or 0),
        "cache_write_tokens": 0 if est else int((usage or {}).get("cache_write_tokens") or 0),
        "reasoning_tokens": 0 if est else int((usage or {}).get("reasoning_tokens") or 0),
        **usage_context.get(),
    }
    for fn in _usage_listeners:
        try:
            fn(rec)
        except Exception:  # noqa: BLE001 - accounting must never break a reply
            pass

DEFAULT_SETTINGS: dict[str, Any] = {
    # Empty until onboarding (or an upgrade from a stored baseUrl). Nothing here is a model alias that only
    # one provider knows: an unsaved defaultModel is filled per provider (providers.default_model, in app.settings()).
    "baseUrl": "",
    "apiKey": "",
    "defaultModel": "",
    # Preset id from providers.py, or None to infer it from baseUrl. onboardedAt is the ISO time setup finished.
    "provider": None,
    "onboardedAt": None,
    "systemPrompt": (
        "You are the assistant inside the user's personal AI OS. Be direct, concise, and useful. "
        "Use markdown when it helps. You may be given memories, a knowledge graph, and file "
        "excerpts as context; use them when relevant and don't mention them unless asked.\n\n"
        "## Working style\n"
        "- Be direct: answer what was asked, with no preamble and without restating the question. When what is already in "
        "front of you answers the question, answer it in plain text — do not make tool calls or retrieve context that would "
        "not change the answer.\n"
        "- When you hand work to a subagent or background worker, say so in your reply and name what is running, so a reply "
        "never comes back as bare tool calls with no visible output."
    ),
    "extractionModel": "",  # "" = the low tier (app.settings() fills it); a saved value overrides
    # Model tiers (providers.tier_model): "" = the active provider's default for that tier.
    "modelHigh": "",
    "modelMedium": "",
    "modelLow": "",
    "fastModel": "",  # what Auto sends a short, plain message to (router.py); empty means Auto uses the default model
    "autoRoute": False,  # new chats start on Auto: the fast or the default model per message
    "consolidateEvery": CONSOLIDATE_EVERY,  # propose a memory tidy-up after this many new auto memories; 0 = manual only
    "autoLearn": True,
    "followUps": True,  # up to 3 suggested next questions under the latest reply (uses the extraction model)
    "autoTitle": True,  # a short model-written chat title after the first reply (uses the extraction model)
    # Pre-image copies of local files the agent overwrites or moves, so Undo works (filesnap.py).
    "fileSnapshots": True,
    "fileSnapshotMaxBytes": FILE_SNAPSHOT_MAX_BYTES,
    "fileSnapshotRetainDays": FILE_SNAPSHOT_RETAIN_DAYS,
    "fileSnapshotBudgetMB": FILE_SNAPSHOT_BUDGET_MB,
    # Every permission key (tools, alwaysAsk, permissionRules, skipPermissions, ...) and its default: permissions.py.
    **PERMISSION_DEFAULTS,
    "toolReadRetries": TOOL_READ_RETRIES,  # extra attempts for a read-only tool after a transient network error (0 = never retry)
    "parallelReads": 0,  # read-only calls of one round that run together; 0 = automatic (limits.worker_slots), 1 = one at a time
    "stuckDetection": True,  # legacy: no longer read, stuck detection (stuck.py) is always on
    # Bank long messages the user writes as style samples and keep their voice profile current (style.py).
    # Independent of autoLearn: wanting the app to learn facts is not the same as wanting it to copy your voice.
    "learnStyle": True,
    "theme": "dark",
    "accent": "sage",
    "mode": "classic",
    "gatherShortcut": "Control+Alt+Command+Space",
    # Global quick capture: a small window that appends a timestamped bullet to today's daily note.
    "quickCaptureShortcut": "CommandOrControl+Shift+Space",
    "quickAskShortcut": "Alt+Space",
    # Hold this in the chat box to dictate while held; a quick tap latches it on.
    "dictationChord": "Control+Alt+D",
    # Shell modularity: Today-screen cards ({key: bool}, missing = shown) and sidebar views the user removed.
    "homeWidgets": {},
    "hiddenViews": [],
    # Rows hidden from the sidebar only (home, docs, spaces, projects, jobs); they stay reachable everywhere else.
    "sidebarHidden": [],
    # {view: "sidebar" | "apps"}; missing = the module's own default placement.
    # Bump when the default-off set changes so existing DBs pick up the change once.
    "modulesDefault": 5,
    "snapshotsEnabled": True,
    # Keep the system prompt identical between turns and put per-turn retrieval just before the newest
    # user message, so the provider's prefix cache survives (context.layout_messages).
    "cacheLayout": True,
    # Show traces, the context preview, the full system prompt and OTLP export in the UI. Traces are recorded either way.
    "devTools": False,
    # Context management (compaction.py). Window and thresholds are estimates (len//4), not provider counts.
    "contextWindow": 0,  # 0 = automatic: the proxy's figure for the model, else limits.CONTEXT_WINDOW_FALLBACK; non-zero overrides
    "autoCompact": True,
    "compactAt": COMPACT_AT,
    "compactKeepRecent": COMPACT_KEEP_RECENT,
    "compactAtTokens": COMPACT_AT_TOKENS,  # absolute trigger on history alone, for windows too large for the fraction to ever fire
    "compactKeepTokens": COMPACT_KEEP_TOKENS,
    "microAtTokens": MICRO_AT_TOKENS,
    "microKeep": MICRO_KEEP,
    "microAt": MICRO_AT,  # old tool results stub out past a quarter of the window: past ~30k tokens a round, time to first token dominates
    # Opt-in OpenTelemetry GenAI export (otel_export.py). Off by default; replaced whole through PUT /settings.
    # Loopback endpoints only unless allowRemote; no message content unless includeContent.
    "otelExport": {"enabled": False, "endpoint": "", "headers": {}, "includeContent": False, "allowRemote": False, "timeoutSeconds": 5},
    # Offer MCP tools through mcp_tool_search once more than this many are ready (0 = always send every schema).
    "mcpDeferAbove": MCP_DEFER_ABOVE,
    # Past this many built-in tools, offer the core set plus tool_search instead of every schema (0 = send them all).
    "toolDeferAbove": TOOL_DEFER_ABOVE,
    # Put the notes each connected MCP server sends at initialize into the prompt (fenced, scanned, capped).
    "mcpServerNotes": True,
    # Provider resilience (retry/backoff section below). Retries only happen before a reply's first token;
    # llmIdleSeconds is how long a stream may go without a byte before it is abandoned with a clear error.
    "llmRetries": LLM_RETRIES,
    "llmIdleSeconds": LLM_IDLE_SECONDS,  # a reasoning model can think a long while before its first token
    # Retention (retention.py): days of history kept in tables that only ever grow. User content is never pruned.
    "retainUsageDays": RETAIN_USAGE_DAYS,
    "retainTraceDays": RETAIN_TRACE_DAYS,
    "retainToolResultDays": RETAIN_TOOL_RESULT_DAYS,
    "retainApprovalDays": RETAIN_APPROVAL_DAYS,
    # Cowork desks. deskMaxLive bounds how many desks may be running at once.
    # How long a desk waits on a card nobody is watching before letting the run go. The card stays
    # pending and decidable; only the run lets go. 0 = wait forever, which is what a chat does.
    "parkAfterSeconds": DESK_PARK_AFTER_SECONDS,
    "deskMaxLive": 0,  # 0 = automatic (limits.worker_slots); a non-zero value overrides
    # Relaunch desks a restart interrupted mid-turn. Off by default: a desk with a call whose outcome is
    # unknown, or one waiting on an approval or its plan, is never relaunched either way.
    "deskAutoResume": False,
    # A new chat's first message starts it as a task (a desk) that works through its steps; a plain question is answered
    # and the desk settles done. The composer's Autonomous switch starts from this value.
    "autonomousByDefault": True,
    # Subagents (subagents.py): how many may run at once across the app and how deep they may nest. Hang
    # detection: a child with no model or tool activity for subagentStaleSeconds, or stuck inside one tool for
    # subagentToolSeconds, is stopped and returns what it had.
    "subagentMaxConcurrent": 0,  # 0 = automatic (limits.worker_slots); a non-zero value overrides
    "subagentMaxDepth": SUBAGENT_MAX_DEPTH,
    "subagentStaleSeconds": SUBAGENT_STALE_SECONDS,
    "subagentToolSeconds": SUBAGENT_TOOL_SECONDS,
    # Workers (workers.py): the chat's front agent hands multi-step work to detached background workers on the chat's own
    # model. delegationForce routes (never stops) work: after delegationAfterRounds rounds of tool calls in one reply the
    # reply may only delegate and answer. workerMaxConcurrent workers run at once; the rest queue in order.
    "delegationForce": True,
    "delegationAfterRounds": DELEGATION_AFTER_ROUNDS,
    "workerMaxConcurrent": WORKER_MAX_CONCURRENT,
    # Workflows (workflows.py): the most items one fan-out step may map over.
    "workflowMaxFanOut": WORKFLOW_MAX_FAN_OUT,
    # Scheduled-job run policy (jobs_policy.py): retry backoff base in seconds (doubles per attempt, capped at
    # 30 min) and how many consecutive failed fires switch a job off.
    "jobRetryBackoffS": JOB_RETRY_BACKOFF_S,
    "jobFailureStreakLimit": JOB_FAILURE_STREAK_LIMIT,
    "proposalExpireDays": PROPOSAL_EXPIRE_DAYS,  # a job's pending proposal turns 'expired' (no longer acceptable) after this many days; 0 = never
    "jobExpireDays": JOB_EXPIRE_DAYS,  # recurring jobs pause (reason "expired") after one last fire this many days after arming; 0 = never
    # OS notification when an unattended job fails, is paused, or leaves proposals (only while the app is hidden).
    "notifyJobs": True,
    # A system notification when a desk needs you or finishes, while the window is not focused.
    "deskNotify": True,
    "chatNotify": True,
    # Spaces: a chat window shows the chat's face instead of the transcript until switched.
    "compactChats": False,
    # A floating Explain / Summarize / Verify / Ask bubble over selected text.
    "selectionToolbar": True,
    # Interface zoom, percent (80-160 in steps of 5); every window applies it as its page zoom factor.
    "uiZoom": 110,
    # Default type for Files ({font: serif|sans|mono|book, size: px, measure: ch}); a doc can override it (docs.typography).
    "docTypography": {},
    "responseStyle": "default",  # what a new chat starts on; see style_presets
    "responseStyleText": "",
    # Undo window on outgoing mail (outbox.py). `seconds` is clamped to 60-120 on read.
    "gmailSendHold": {"enabled": True, "seconds": GMAIL_SEND_HOLD_SECONDS},
    # When set (or when the FIRECRAWL_API_KEY environment variable is), Firecrawl answers web_search and fetch_url first; the engines below are the fallback.
    "firecrawlApiKey": "",
    "braveApiKey": "",
    "tavilyApiKey": "",
    # Without a Brave/Tavily key, web_search uses Exa (keyless via its hosted MCP server; a key lifts the rate limit).
    "exaApiKey": "",
    # Base URL of your own SearXNG (needs `json` under search.formats); empty = off. It runs beside Exa and the results are merged.
    "searxngUrl": "",
    # fetch_url retries a blocked or JavaScript-only page through Jina Reader (r.jina.ai), which then sees the URL.
    "readerFallback": True,
    # fetch_url reuses a page it fetched this many seconds ago (0 = never); fresh=true on the call bypasses it.
    "fetchCacheSeconds": FETCH_CACHE_SECONDS,
    # github_search/github_read; empty = the gh CLI's login (`gh auth token`), else unauthenticated (60 requests/h).
    "githubToken": "",
    # A stopped sandbox (containers are stopped, not removed, at app quit) is deleted after this many idle days.
    "sandboxKeepDays": SANDBOX_KEEP_DAYS,
    # Mount the active desk's workspace read-write at /workspace/desk in that desk's sandbox container.
    "sandboxMountDesk": True,
    # fs_edit and an overwriting write_local_file refuse a file this conversation has not read (or that changed since).
    "requireReadBeforeWrite": True,
    # Host shell (shell.py): shell_run runs in a Seatbelt sandbox: any folder, minus Grain's own data and the credential stores.
    "shellTimeoutSec": SHELL_TIMEOUT_SECONDS,      # foreground default; a call may ask for up to 600
    "shellMaxBackground": SHELL_MAX_BACKGROUND,     # live background jobs at once
    "codingSessionMaxConcurrent": CODING_SESSION_MAX_CONCURRENT,    # live coding sessions at once (own pool)
    # The model view_image sends pictures to. Empty = the chat model, when the provider says it reads images.
    "visionModel": "",
    # The model generate_image calls (POST /images/generations, OpenAI shape). Empty = the tool says it is not set up.
    "imageModel": "",
    # The agent's own browser (browser.py): interactive pages in a separate cookie jar, driven from a desk or chat.
    "browserMaxTabs": BROWSER_MAX_TABS,
    "browserIdleSeconds": BROWSER_IDLE_SECONDS,
    # Extra packages installed into the shared work environment (envs.py) beside its base set.
    "workEnvPackages": [],
    # {model: {"input": $/M tokens, "output": $/M tokens}} overrides for cost accounting (proxy prices are used otherwise)
    "modelPrices": {},
    "googleClientId": "",
    "googleClientSecret": "",
    "googleToken": {},
    # Entra public client (PKCE, no secret). Tenant "" means "common".
    "microsoftClientId": "",
    "microsoftTenant": "",
    "microsoftToken": {},
    # Which account Mail, Calendar, the mail/calendar tools, the reply tracker and the outbox use: "google" | "microsoft".
    "pimProvider": "google",
    # Google Tasks <-> todos sync. Shape and defaults live in gtasks.DEFAULT_CONFIG; patched
    # through /integrations/google/tasks-sync rather than /settings for the same reason.
    # Empty on purpose: anything named here would override that module's defaults.
    "googleTasksSync": {},
    # Document retrieval (retrieval.py). 'bm25' forces keyword-only; hybrid falls back to it when the
    # embedding route is unavailable. The floor only drops vector-only hits (exact keyword hits survive).
    "retrievalMode": "hybrid",
    "embeddingModel": "qwen3-embedding-8b",
    "retrievalMinSimilarity": RETRIEVAL_MIN_SIMILARITY,
    # Memories: fuse BM25 + embeddings + recency + graph (memory_index.py). Needs embeddingModel; false = keyword-only.
    "hybridRetrieval": True,
    "retrievalPerDocCap": RETRIEVAL_PER_DOC_CAP,
    "retrievalCandidates": RETRIEVAL_CANDIDATES,
    # Off by default, one model call per chunk: new uploads and embed-backfill (Rebuild index) write a short blurb situating each chunk in
    # its document, which is then indexed and embedded with the chunk. Rerank: reorder the fused candidates
    # with a rerank model (/v1/rerank, else one completion) before trimming. The model is shared by documents and memory;
    # blank = the provider's default (providers.rerank_model), and none known = reranking is skipped.
    "contextualChunks": False,
    "retrievalRerank": False,
    "retrievalRerankModel": "",
    # Memories: reorder the fused candidates with the rerank model; the fused order stands on a timeout or error.
    "memoryRerank": True,
    # Also retrieve from the user's own editor files (not just uploaded files) when a chat has useDocuments on.
    "useDocsInContext": True,
    # Reply tracker (mailwatch.py); MailWatchModule.config() merges stored values over these defaults.
    "mailWatch": {"awaitingAfterDays": 3, "needsReplyAfterHours": 24, "useLLM": False,
                  "query": "newer_than:14d -category:promotions -category:social"},
    # Text Grain from your own phone over a Telegram bot (telegram.py). Off until a token is saved and a chat is paired.
    "telegramEnabled": False,
    "telegramNotifyLongRuns": False,  # also send approvals and finish notices for runs that were not started from Telegram
    "telegramLongRunMinutes": TELEGRAM_LONG_RUN_MINUTES,
    "telegramPushWorkerResults": False,  # also text the owner the reply Grain writes when a background worker finishes
    # Todo time-block planner (planner.py); PlannerModule.config() merges stored values over these.
    "planner": {"workStart": "09:00", "workEnd": "17:30", "workDays": [1, 2, 3, 4, 5], "bufferMin": 10, "minBlockMin": 15,
                "maxBlockMin": 120, "slotStepMin": 15, "lookaheadDays": 7, "calendarName": "Grain Todos"},
}




class LLMError(Exception):
    """A provider or transport failure with a readable message. `kind` is the machine-readable class (see
    classify_error; also transport, cancelled, timeout) and `status` the HTTP status, when there was one."""

    def __init__(self, message: str = "", kind: str | None = None, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


class ContextOverflowError(LLMError):
    """The conversation does not fit the model. `limit` is the window the provider named, when it did."""

    def __init__(self, message: str = "", kind: str | None = "overflow", status: int | None = None, limit: int | None = None):
        super().__init__(message, kind, status)
        self.limit = limit


NOT_CONFIGURED = "No AI provider is set up yet. Open Settings and choose one."


def _url(settings: dict[str, Any], path: str, model: str | None = None) -> str:
    base = str(settings.get("baseUrl") or "").strip()
    if not base:
        raise LLMError(NOT_CONFIGURED)
    if model is not None and not model.strip():
        raise LLMError("No model is selected. Pick one in Settings.")
    return providers.endpoint(base, path)


def supports_service_tier(settings: dict[str, Any]) -> bool:
    """Only OpenAI-shaped priority routing; elsewhere an unknown field can fail the whole request."""
    return providers.effective(settings) in ("openai", "litellm", "custom")
# ---------------- retry / timeouts ----------------
# A transient failure (rate limit, provider 5xx, dropped connection) is retried with exponential backoff and
# jitter, but ONLY while nothing has been streamed to the caller: after the first token a retry would repeat
# or contradict text the user already read, so a mid-reply failure is reported instead.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 529})
# What is worth retrying is decided by the classified kind, not the status alone: a 429 that says the account is
# out of quota is `quota`, and waiting does not fix it.
RETRYABLE_KINDS = frozenset({"rate_limit", "overloaded", "server", "transport"})
_PERMANENT_KINDS = frozenset({"overflow", "quota", "auth", "not_found", "content_filter", "unsupported_param"})
RETRY_BASE_S = 1.0
RETRY_CAP_S = 30.0
# A Retry-After longer than this is not worth holding a reply open for; say so instead.
RETRY_AFTER_MAX_S = 60.0
CONNECT_TIMEOUT_S = 10.0
DEFAULT_IDLE_S = float(LLM_IDLE_SECONDS)


def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    """Retry-After as seconds: either a delay in seconds or an HTTP date. None when absent or unreadable."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None
    return max(0.0, when - (time.time() if now is None else now))


_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def _parse_duration(value: str | None) -> float | None:
    """A rate-limit reset such as `250ms`, `1s`, `6m0s` or a bare number of seconds, as seconds."""
    if not value:
        return None
    value = value.strip().lower()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    parts = _DURATION.findall(value)
    if not parts or "".join(n + u for n, u in parts) != value:
        return None
    scale = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    return sum(float(n) * scale[u] for n, u in parts)


def retry_after_from(headers: Any, now: float | None = None, status: int | None = None) -> float | None:
    """Seconds to wait from a response's headers: `retry-after-ms`, else `retry-after`, else (only for a 429, or when
    `status` is not given) the smaller of the `x-ratelimit-reset-requests` / `x-ratelimit-reset-tokens` durations.
    Those two describe a full bucket reset and ride on every response, so on a 5xx they say nothing about when to
    retry and would only trip the too-long rule. None when none is readable."""
    get = headers.get
    ms = get("retry-after-ms")
    if ms:
        try:
            return max(0.0, float(ms.strip()) / 1000.0)
        except ValueError:
            pass
    secs = parse_retry_after(get("retry-after"), now)
    if secs is not None:
        return secs
    if status is not None and status != 429:
        return None
    resets = [d for d in (_parse_duration(get("x-ratelimit-reset-requests")), _parse_duration(get("x-ratelimit-reset-tokens"))) if d is not None]
    return min(resets) if resets else None


def retry_delay(attempt: int, retry_after: float | None = None, rand: Callable[[], float] = random.random) -> float:
    """Seconds to wait before retry number `attempt` (1-based): exponential backoff with jitter, floored at Retry-After."""
    backoff = min(RETRY_CAP_S, RETRY_BASE_S * 2 ** (attempt - 1))
    return max(backoff / 2 + rand() * backoff / 2, retry_after or 0.0)


def _retries(settings: dict[str, Any]) -> int:
    v = settings.get("llmRetries", DEFAULT_SETTINGS["llmRetries"])
    return max(0, min(int(v), 10)) if isinstance(v, (int, float)) and not isinstance(v, bool) else 3


def _idle_s(settings: dict[str, Any]) -> float:
    v = settings.get("llmIdleSeconds", DEFAULT_SETTINGS["llmIdleSeconds"])
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else DEFAULT_IDLE_S


def _provider_message(body: str) -> str:
    """The provider's own explanation, when its error body has one in the usual shape."""
    try:
        err = json.loads(body).get("error")
        msg = err.get("message") if isinstance(err, dict) else err
        if isinstance(msg, str) and msg.strip():
            return msg.strip()[:300]
    except (ValueError, AttributeError):
        pass
    return " ".join(body.split())[:300]


_PERMANENT_FRAME = frozenset({"invalid_request_error", "authentication_error", "invalid_api_key", "permission_error", "not_found_error",
                              "model_not_found", "context_length_exceeded", "content_filter", "content_policy_violation", "insufficient_quota"})


def _frame_status(err: Any) -> int | None:
    """The HTTP status an error frame carries as its `code` (or `status`), when it is one; a gateway relays the
    upstream status that way, so classify_error can read it like a real response."""
    if not isinstance(err, dict):
        return None
    for key in ("code", "status"):
        try:
            n = int(err.get(key))
        except (TypeError, ValueError):
            continue
        if 400 <= n < 600:
            return n
    return None


def _frame_error(err: Any) -> tuple[str, bool]:
    """(text, retryable) for an error object that arrived inside a 200 stream. The text is never empty or 'None'."""
    if not isinstance(err, dict):
        return (str(err)[:300] or "Provider error"), True
    code, typ = err.get("code"), err.get("type")
    meta = err.get("metadata")
    raw = meta.get("raw") if isinstance(meta, dict) else None
    msg = err.get("message")
    if isinstance(msg, str) and msg.strip():
        text = msg.strip()[:300]
    elif isinstance(raw, str) and raw.strip():
        text = raw.strip()[:300]
    elif code or typ:
        text = "Provider error " + " ".join(f"({x})" for x in (code, typ) if x)
    else:
        text = json.dumps(err)[:300]
    status = _frame_status(err)
    if status is not None and 400 <= status < 500 and status not in RETRYABLE_STATUS:
        return text, False
    if str(code or "").lower() in _PERMANENT_FRAME or str(typ or "").lower() in _PERMANENT_FRAME:
        return text, False
    return text, True


def _error_fields(body: str) -> tuple[str, str, str]:
    """(code, type, message) of a provider error body, lower-cased; tolerates non-JSON and a string `error`."""
    try:
        err = json.loads(body)
        err = err.get("error", err) if isinstance(err, dict) else err
    except (ValueError, AttributeError, TypeError):
        return "", "", str(body or "").lower()
    if isinstance(err, dict):
        return str(err.get("code") or "").lower(), str(err.get("type") or "").lower(), str(err.get("message") or "").lower()
    return "", "", str(err or "").lower()


_FILTER_MSG = re.compile(r"content policy|safety system|content management|content[_ ]filter")
_OVERFLOW_MSG = re.compile(r"context (length|window)|maximum context|prompt is too long|too many tokens|reduce the length|n_ctx")
_QUOTA_MSG = re.compile(r"exceeded your current quota|insufficient (quota|credits?|balance)|credit balance|billing|payment required")
_PARAM_MSG = re.compile(r"unsupported (parameter|value)|unknown parameter|unrecognized request argument|does not support")
_OVERLOAD_MSG = re.compile(r"overloaded|at capacity")
# A LiteLLM proxy answers 400 "No connected db." when the key is not its master key.
_AUTH_MSG = re.compile(r"no connected db|invalid api key|authentication")


def classify_error(status: int | None, body: str) -> str:
    """The machine-readable class of a provider failure; first matching rule wins. `status` is None for an
    error frame inside a stream."""
    code, typ, msg = _error_fields(body)
    if code in ("content_filter", "content_policy_violation") or typ in ("content_filter", "content_policy_violation") or _FILTER_MSG.search(msg):
        return "content_filter"
    if status == 413 or code == "context_length_exceeded" or _OVERFLOW_MSG.search(msg):
        return "overflow"
    if status == 402 or code == "insufficient_quota" or typ == "insufficient_quota" or code.startswith("billing") or typ.startswith("billing") or _QUOTA_MSG.search(msg):
        return "quota"
    if status in (401, 403) or typ == "no_db_connection" or _AUTH_MSG.search(msg):
        return "auth"
    if status == 404:
        return "not_found"
    if (status in (400, 422) and code in ("unsupported_parameter", "unsupported_value", "unknown_parameter")) or (status in (400, 422, None) and _PARAM_MSG.search(msg)):
        return "unsupported_param"
    if status == 429:
        return "rate_limit"
    if status == 529 or (status in (500, 503) and _OVERLOAD_MSG.search(msg)):
        return "overloaded"
    if status is not None and (status in (408, 425) or status >= 500):
        return "server"
    return "bad_request"


def parse_context_limit(body: str) -> int | None:
    """The context window a provider's overflow message names, when it names one (1,000 to 4,000,000 tokens)."""
    text = _provider_message(body) if body else ""
    for pat in (r"maximum context length is (\d[\d,_]*)", r"(\d[\d,_]*) tokens? *> *(\d[\d,_]*) maximum",
                r"context window of (\d[\d,_]*)", r"n_ctx[=: ]+(\d[\d,_]*)"):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            n = int(re.sub(r"[,_]", "", m.group(m.lastindex)))
            if 1_000 <= n <= 4_000_000:
                return n
    return None


def _provider_error(message: str, kind: str | None, status: int | None, body: str) -> LLMError:
    """LLMError for a classified failure; an overflow carries the parsed window."""
    if kind == "overflow":
        return ContextOverflowError(message, kind, status, parse_context_limit(body))
    return LLMError(message, kind, status)


def describe_http_error(status: int, reason: str, body: str, retried: int = 0, retry_after: float | None = None, kind: str | None = None) -> str:
    """A sentence a person can act on, for a failed provider response."""
    tail = f" Retried {retried} time{'s' if retried != 1 else ''}." if retried else ""
    detail = _provider_message(body)
    if kind == "quota":
        return f"The provider says you are out of quota or credit ({status}). Check billing with the provider, or pick another model. {detail}".strip()
    if kind == "overflow":
        return f"This conversation is too long for the model ({status}). Compact it or start a new chat. {detail}".strip()
    if kind == "unsupported_param":
        return f"The model rejected a request option ({status}). Set effort to Default, turn off Fast, or pick another model. {detail}".strip()
    if kind == "content_filter":
        return f"The provider's content filter blocked the request ({status}). Rephrase it or pick another model. {detail}".strip()
    if kind == "overloaded":
        return f"The provider is overloaded ({status}).{tail} Try again shortly or pick another model. {detail}".strip()
    if status == 429:
        wait = f" The provider asked to wait {int(retry_after)}s." if retry_after else ""
        more = f" {detail}" if detail and kind == "rate_limit" else ""
        return f"The provider is rate-limiting you (429).{tail}{wait} Wait a moment and try again, or pick another model.{more}"
    if kind == "auth" or status in (401, 403):
        return f"The provider rejected your API key or access ({status}). Check the API key in Settings. {detail}".strip()
    if status == 404:
        return f"The provider does not know that model or route (404). Check the model name in Settings. {detail}".strip()
    if status in RETRYABLE_STATUS or status >= 500:
        return f"The provider is having trouble ({status} {reason}).{tail} Try again shortly. {detail}".strip()
    return f"{status} {reason}: {detail}".strip()


def describe_transport_error(e: BaseException, retried: int = 0) -> str:
    tail = f" Retried {retried} time{'s' if retried != 1 else ''}." if retried else ""
    if isinstance(e, httpx.ConnectTimeout):
        return f"Could not reach the model provider: the connection timed out.{tail} Check your network and the base URL in Settings."
    if isinstance(e, httpx.ConnectError):
        return f"Could not reach the model provider.{tail} Check your network, and that the base URL in Settings is running."
    if isinstance(e, httpx.TimeoutException):
        return f"The model provider stopped responding.{tail} Try again."
    return f"The connection to the model provider failed ({type(e).__name__}).{tail} Try again."


async def _backoff(delay: float, cancel: asyncio.Event | None = None) -> bool:
    """Sleep `delay` seconds. False when `cancel` fired first, so a Stop is not held hostage by a backoff."""
    if cancel is None:
        await asyncio.sleep(delay)
        return True
    try:
        await asyncio.wait_for(cancel.wait(), delay)
    except asyncio.TimeoutError:
        return True
    return False


class _Aborted(Exception):
    """The caller's Stop (`reason` 'cancelled') or the run's deadline ('timeout') ended a provider call. Private and
    not an LLMError, so it cannot be stored on a message as an error by accident."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


async def _race(aw: Any, cancel: asyncio.Event | None, deadline_at: float | None) -> Any:
    """Await `aw`, but give up the moment `cancel` is set or `deadline_at` (a time.monotonic() value) passes."""
    task = asyncio.ensure_future(aw)
    waiter = asyncio.ensure_future(cancel.wait()) if cancel is not None else None
    left = None if deadline_at is None else max(deadline_at - time.monotonic(), 0.0)
    try:
        await asyncio.wait({task, *([waiter] if waiter is not None else [])}, timeout=left, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            return task.result()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        raise _Aborted("cancelled" if cancel is not None and cancel.is_set() else "timeout")
    except asyncio.CancelledError:
        task.cancel()
        raise
    finally:
        if waiter is not None:
            waiter.cancel()


# What this provider told us a model cannot take, keyed 'base|model': {effort: 'none' | 'high', at: epoch}. Learned from
# a rejected reasoning_effort, persisted by the app (private setting `modelCaps`), and forgotten after 30 days so a
# wrong or outdated entry heals.
CAPS_TTL_S = 30 * 86400.0
_model_caps: dict[str, dict[str, Any]] = {}
_caps_listeners: list[Callable[[dict[str, dict[str, Any]]], None]] = []


def _caps_key(base_url: str | None, model: str) -> str:
    return f"{str(base_url or '').strip().rstrip('/')}|{model}"


def load_caps(saved: Any) -> None:
    _model_caps.clear()
    for k, v in (saved.items() if isinstance(saved, dict) else []):
        if isinstance(v, dict) and v.get("effort") in ("none", "high") and isinstance(v.get("at"), (int, float)):
            _model_caps[str(k)] = {"effort": v["effort"], "at": float(v["at"])}


def model_cap(base_url: str | None, model: str) -> dict[str, Any] | None:
    e = _model_caps.get(_caps_key(base_url, model))
    return e if e and time.time() - float(e.get("at") or 0) < CAPS_TTL_S else None


def on_caps(fn: Callable[[dict[str, dict[str, Any]]], None]) -> None:
    _caps_listeners.append(fn)


def _learn_cap(settings: dict[str, Any], model: str, effort: str) -> None:
    prev = model_cap(settings.get("baseUrl"), model)
    if prev and prev["effort"] == effort:
        return
    _model_caps[_caps_key(settings.get("baseUrl"), model)] = {"effort": effort, "at": time.time()}
    for fn in list(_caps_listeners):
        try:
            fn({k: dict(v) for k, v in _model_caps.items()})
        except Exception:  # noqa: BLE001 - persisting a hint must never fail a reply
            log.exception("model caps listener failed")


_EFFORT_MSG = ("reasoning_effort", "reasoning effort", "does not support thinking", "does not support reasoning")


def _rejected_optional(status: int, text: str, body: dict[str, Any]) -> str | None:
    """Which optional request field a 400/422 names as the problem ('reasoning_effort' or 'service_tier'), if it is
    in `body`. A rejection that does not name the field is not ours to retry."""
    if status not in (400, 422):
        return None
    _, _, msg = _error_fields(text)
    param = ""
    try:
        err = json.loads(text).get("error")
        param = str(err.get("param") or "").lower() if isinstance(err, dict) else ""
    except (ValueError, AttributeError, TypeError):
        pass
    if "reasoning_effort" in body and (param == "reasoning_effort" or any(m in msg for m in _EFFORT_MSG)):
        return "reasoning_effort"
    if "service_tier" in body and (param == "service_tier" or "service_tier" in msg or "service tier" in msg):
        return "service_tier"
    if "user" in body and param == "user":  # the affinity id is an optimisation, never worth a failed reply
        return "user"
    return None


async def _close_cm(cm: Any) -> None:
    if cm is not None:
        with contextlib.suppress(Exception):
            await cm.__aexit__(None, None, None)


def _retry_event(attempt: int, retries: int, delay: float, reason: str, status: int | None) -> dict[str, Any]:
    """The `retry` event announcing a backoff that is about to start (display only; nothing reads it back)."""
    return {"type": "retry", "attempt": attempt, "max": retries, "delay_s": delay, "reason": reason, "status": status}


async def _send_attempts(client: httpx.AsyncClient, settings: dict[str, Any], body: dict[str, Any], *, stream: bool,
                         cancel: asyncio.Event | None = None, deadline_at: float | None = None,
                         attempt: int = 0) -> AsyncIterator[dict[str, Any]]:
    """POST the completion request, retrying 429/5xx/connection failures. Yields a `retry` dict just before each
    backoff (see `_retry_event`) and ends with {"type": "response", "response", "cm", "attempt"}: the response, the
    stream context or None, and the retries used. Every response of a retried attempt is closed before its `retry` is
    yielded, so a consumer that stops reading holds no socket.

    `attempt` is the retries already spent, so header-level and stream-level retries share the one `llmRetries` cap.
    A streamed response comes back open: the caller closes it through the context. A non-2xx answer that survives
    the retries (or is not retryable) raises LLMError with a readable message. Stop or the deadline raise _Aborted,
    during the header wait as well as between attempts; a backoff that would cross the deadline is not slept, the
    provider's own error is raised instead. A 400/422 that names `reasoning_effort` or `service_tier` is resent with
    the field stepped down or dropped, without using an attempt; the body is edited in place, so the caller can see
    what was dropped.

    A key the provider already rejected fails here before any request (auth_breaker), with kind "auth" and no status.
    """
    retries = _retries(settings)
    url = _url(settings, "/chat/completions", body.get("model"))
    if (held := auth_breaker.check(settings)) is not None:
        raise LLMError(held, kind="auth")
    stepped = dropped = False
    sent_effort = body.get("reasoning_effort")
    while True:
        if cancel is not None and cancel.is_set():
            raise _Aborted("cancelled")
        cm = client.stream("POST", url, headers=_headers(settings), json=body) if stream else None
        try:
            if cm is not None:
                r = await _race(cm.__aenter__(), cancel, deadline_at)
                if r.status_code >= 400:
                    await _race(r.aread(), cancel, deadline_at)
            else:
                r = await _race(client.post(url, headers=_headers(settings), json=body), cancel, deadline_at)
        except _Aborted:
            await _close_cm(cm)
            raise
        except (httpx.TransportError, httpx.ProtocolError) as e:
            await _close_cm(cm)
            if cancel is not None and cancel.is_set():
                raise _Aborted("cancelled") from e
            delay = retry_delay(attempt + 1)
            if attempt >= retries or (deadline_at is not None and time.monotonic() + delay >= deadline_at):
                raise LLMError(describe_transport_error(e, attempt), kind="transport") from e
            attempt += 1
            log.warning("provider connection failed (%s); retry %d/%d in %.1fs", type(e).__name__, attempt, retries, delay)
            yield _retry_event(attempt, retries, delay, "connection", None)
            if not await _backoff(delay, cancel):
                raise _Aborted("cancelled") from e
            continue
        if r.status_code < 400:
            auth_breaker.success(settings)
            if sent_effort and (stepped or dropped):
                _learn_cap(settings, str(body.get("model") or ""), "none" if dropped else "high")
            yield {"type": "response", "response": r, "cm": cm, "attempt": attempt}
            return
        retry_after = retry_after_from(r.headers, status=r.status_code)
        kind = classify_error(r.status_code, r.text)
        await _close_cm(cm)
        auth_breaker.failure(settings, r.status_code, r.text)
        field = _rejected_optional(r.status_code, r.text, body)
        if field == "reasoning_effort" and body[field] in ("xhigh", "max") and not stepped:
            stepped = True
            body[field] = "high"
            log.warning("provider rejected reasoning_effort %s; resending with high", sent_effort)
            continue
        if field:
            dropped = dropped or field == "reasoning_effort"
            body.pop(field, None)
            log.warning("provider rejected %s; resending without it", field)
            continue
        too_long = retry_after is not None and retry_after > RETRY_AFTER_MAX_S
        if kind in RETRYABLE_KINDS and attempt < retries and not too_long:
            delay = retry_delay(attempt + 1, retry_after)
            if deadline_at is None or time.monotonic() + delay < deadline_at:
                attempt += 1
                log.warning("provider answered %d; retry %d/%d in %.1fs", r.status_code, attempt, retries, delay)
                yield _retry_event(attempt, retries, delay, "rate_limit" if r.status_code == 429 else "provider_error", r.status_code)
                if not await _backoff(delay, cancel):
                    raise _Aborted("cancelled")
                continue
        raise _provider_error(describe_http_error(r.status_code, r.reason_phrase, r.text, attempt, retry_after, kind), kind, r.status_code, r.text)


async def _send_with_retry(client: httpx.AsyncClient, settings: dict[str, Any], body: dict[str, Any], *, stream: bool,
                           cancel: asyncio.Event | None = None, deadline_at: float | None = None,
                           attempt: int = 0) -> tuple[httpx.Response, Any, int]:
    """`_send_attempts` drained: (response, stream context or None, retries used), for callers with no use for the retry events."""
    async for ev in _send_attempts(client, settings, body, stream=stream, cancel=cancel, deadline_at=deadline_at, attempt=attempt):
        if ev["type"] == "response":
            return ev["response"], ev["cm"], ev["attempt"]
    raise RuntimeError("unreachable: _send_attempts always ends with a response")


def _headers(settings: dict[str, Any]) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if settings.get("apiKey"):
        h["Authorization"] = f"Bearer {settings['apiKey']}"
    if (sid := session_affinity.get()) and _affinity_ok(settings):
        h["x-session-affinity"] = sid
    return h


def _affinity_ok(settings: dict[str, Any]) -> bool:
    """Fireworks reads the header; a LiteLLM proxy ignores it and forwards the `user` field instead (see _with_affinity)."""
    return providers.effective(settings) in ("fireworks", "litellm")


def _with_affinity(settings: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """`user` carries the same id in the body: the proxy passes it to Fireworks, which also routes on it."""
    if (sid := session_affinity.get()) and _affinity_ok(settings):
        body["user"] = sid
    return body


# Ids that name a non-chat model when the proxy reports no `mode` for them: a conservative
# pattern, since the picker still shows whatever model the chat already uses.
_NON_CHAT_ID = ((re.compile(r"embed|rerank", re.I), "embedding"), (re.compile(r"whisper|tts", re.I), "audio"),
                (re.compile(r"moderation", re.I), "moderation"))


async def list_models(settings: dict[str, Any]) -> list[dict[str, Any]]:
    url = _url(settings, "/models")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, headers=_headers(settings))
    if r.status_code >= 400:
        raise LLMError(f"{r.status_code}: {r.text[:300]}")
    data = r.json().get("data", [])
    note_vision_listing(data)
    out: list[dict[str, Any]] = []
    for m in data:
        if "id" not in m:
            continue
        caps = caps_lookup(m["id"])
        row: dict[str, Any] = {"id": m["id"]}
        mode = caps.get("mode") or next((kind for pat, kind in _NON_CHAT_ID if pat.search(m["id"])), None)
        if mode is not None:
            row["mode"] = mode
        reasoning = effort_supported(m["id"], caps)
        if reasoning is not None:
            row["reasoning"] = reasoning
        out.append(row)
    return sorted(out, key=lambda m: m["id"])


# Which models read images, as far as a provider's own model listing says so. Filled as a side effect of list_models
# (the model picker calls it), read synchronously by vision.model_for: a reply never waits on a listing.
_VISION_FLAGS: dict[str, bool] = {}


def note_vision_listing(rows: Any) -> None:
    """Remember the vision flag of each row of a /models listing, for the shapes providers use: an input-modality
    list (`architecture.input_modalities`, `modalities`, `input_modalities`) or a boolean (`supports_vision`,
    `capabilities.vision`). A row that says nothing leaves the model unflagged (the name fallback decides)."""
    if not isinstance(rows, list):
        return
    for m in rows:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        arch = m.get("architecture") if isinstance(m.get("architecture"), dict) else {}
        caps = m.get("capabilities") if isinstance(m.get("capabilities"), dict) else {}
        mods = arch.get("input_modalities") or m.get("input_modalities") or m.get("modalities")
        flag: bool | None = None
        if isinstance(mods, list):
            flag = any(str(x).lower() in ("image", "vision") for x in mods)
        elif isinstance(m.get("supports_vision"), bool):
            flag = m["supports_vision"]
        elif isinstance(caps.get("vision"), bool):
            flag = caps["vision"]
        elif isinstance(m.get("supports_image_input"), bool):
            flag = m["supports_image_input"]
        if flag is not None:
            _VISION_FLAGS[str(m["id"])] = flag


def vision_flag(model: str) -> bool | None:
    """True/False when a provider listing said so, None when it never did."""
    return _VISION_FLAGS.get(model)


def _prompt_chars(messages: list[dict[str, Any]]) -> int:
    """Characters of prompt for the usage estimate. Image parts count as nothing: a base64 picture is megabytes of
    characters but a few hundred tokens, and the provider's own usage figure replaces this estimate when it sends one."""
    slim = [
        {**m, "content": [p for p in m["content"] if not (isinstance(p, dict) and p.get("type") == "image_url")]}
        if isinstance(m.get("content"), list) else m
        for m in messages
    ]
    return len(json.dumps(slim))


# Kimi K3 always thinks and accepts only low, high, and max. Omitting the field is max, and
# medium / xhigh are rejected. High is the middle of that scale, so the app's Medium lands there.
_KIMI_K3_EFFORT = {"low": "low", "medium": "high", "high": "high", "xhigh": "max", "max": "max"}
# DeepSeek V4 takes none/low/medium/high/xhigh/max, but runs low and medium as high and xhigh as max. Name the level it runs.
_DEEPSEEK_V4_EFFORT = {"low": "low", "medium": "high", "high": "high", "xhigh": "max", "max": "max", "off": "none", "none": "none"}
# Without max_tokens Fireworks stops DeepSeek V4 at 2048 tokens, which thinking alone can use up. A per-response
# ceiling, not a budget: a turn still runs as many rounds as it needs.
DEEPSEEK_V4_MAX_TOKENS = 32_768
# Families that want their own reasoning_content back on an assistant tool-call message (interleaved thinking).
# Anything else gets the field stripped: a model that does not know it may reject the request.
_REASONING_ECHO = ("deepseek", "kimi", "glm")


def _model_slug(model: str) -> str:
    return model.rsplit("/", 1)[-1].lower()


# Set to `Pricing.caps` by app.py: what the proxy says a model can do ({mode?, reasoning?, max_input_tokens?}).
caps_lookup: Callable[[str], dict[str, Any]] = lambda _m: {}


def requires_max_tokens(settings: dict[str, Any]) -> bool:
    """Only the Anthropic API refuses a request without max_tokens. Nothing else gets the field."""
    return providers.infer(settings.get("baseUrl")) == "anthropic"


def output_cap(settings: dict[str, Any], model: str) -> int | None:
    """The max_tokens to send: the model's own output maximum for a provider that requires the field, DeepSeek V4's
    ceiling (its provider default truncates thinking replies), else None."""
    if requires_max_tokens(settings):
        return int(caps_lookup(model).get("max_output_tokens") or 0) or None
    return DEEPSEEK_V4_MAX_TOKENS if _model_slug(model).startswith("deepseek-v4") else None


def wire_messages(model: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`messages` as sent: reasoning_content kept for a family that reads it back, dropped for any other."""
    if _model_slug(model).startswith(_REASONING_ECHO) or not any("reasoning_content" in m for m in messages):
        return messages
    return [{k: v for k, v in m.items() if k != "reasoning_content"} for m in messages]


def effort_supported(model: str, caps: dict[str, Any] | None) -> bool | None:
    """Whether the model takes a reasoning level: False for the Kimi K2 family, else what the proxy reports (None = unknown)."""
    if _model_slug(model).startswith("kimi-k2"):
        return False
    return (caps or {}).get("reasoning")


def effort_param(model: str, effort: str, caps: dict[str, Any] | None = None, base_url: str | None = None) -> str | None:
    """The `reasoning_effort` to send, or None to leave the field off.

    `'default'` always omits the field. That is a deliberate choice, not the starting level:
    new chats start at high (`repos.DEFAULT_EFFORT`).
    """
    if not effort or effort == "default":
        return None
    slug = _model_slug(model)
    # K2 does not take this field. K2.7 always thinks; sending the field fails the request.
    if slug.startswith("kimi-k2"):
        return None
    if slug.startswith("kimi-k3"):
        return _KIMI_K3_EFFORT.get(effort, "high")
    if slug.startswith("deepseek-v4"):
        return _DEEPSEEK_V4_EFFORT.get(effort, "high")
    if (caps or {}).get("reasoning") is False:
        return None
    # What this provider already rejected for this model (see _send_with_retry), so a tool loop does not pay a failed request per round.
    learned = model_cap(base_url, model) if base_url else None
    if learned and learned["effort"] == "none":
        return None
    if learned and effort in ("xhigh", "max"):
        return "high"
    return effort


def _reason_text(delta: dict[str, Any]) -> str:
    """Chain-of-thought from a reasoning model. It arrives beside `content`, not inside it.

    kimi, glm and deepseek send `reasoning_content`. Others send `reasoning`, sometimes as a list of
    chunks. A missing field is an empty string, so a normal model changes nothing.
    """
    raw = delta.get("reasoning_content")
    if raw is None:
        raw = delta.get("reasoning")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return str(raw.get("text") or raw.get("content") or "")
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "".join(parts)
    return ""


def _content_text(obj: dict[str, Any]) -> str:
    """The answer text of a delta or message: a str as is, a list of str / {text} parts joined, anything else ''."""
    raw = obj.get("content")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return "".join(i if isinstance(i, str) else i["text"] if isinstance(i, dict) and isinstance(i.get("text"), str) else "" for i in raw)
    return ""


_THINK_OPEN = {"<think>": "</think>", "<thinking>": "</thinking>"}
_THINK_BLOCK = re.compile(r"\s*<(think(?:ing)?)>(.*?)</\1>\s*", re.IGNORECASE | re.DOTALL)


class ThinkSplitter:
    """Routes a model's inline `<think>...</think>` block to reasoning and the rest to the answer.

    One instance per stream. Only a block that opens the reply (after whitespace) counts, so a literal tag later in an
    answer is left alone. feed() and flush() return [("reasoning" | "delta", piece)]; text that might be part of a tag
    split across chunks is held back until the next chunk decides it.
    """

    def __init__(self) -> None:
        self.state = "probe"
        self.held = ""
        self.close = ""
        self.lead = False  # drop leading whitespace (after the open tag) or newlines (after the close tag)

    def feed(self, text: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if self.state == "probe":
            self.held += text
            s = self.held.lstrip().lower()
            tag = next((t for t in _THINK_OPEN if s.startswith(t)), None)
            if tag:
                rest = self.held.lstrip()[len(tag):]
                self.state, self.close, self.held, self.lead = "think", _THINK_OPEN[tag], "", True
                text = rest
            elif not s or any(t.startswith(s) for t in _THINK_OPEN):
                return out
            else:
                self.state, held, self.held = "pass", self.held, ""
                return [("delta", held)]
        if self.state == "think":
            buf = self.held + text
            if self.lead:
                buf = buf.lstrip()
                self.lead = not buf
            # A regex search, not lower().find(): lower() can change a string's length (e.g. a dotted capital I), which
            # would put the index off by a character or two in the original text.
            m = re.search(re.escape(self.close), buf, re.IGNORECASE | re.ASCII)
            i = m.start() if m else -1
            if i >= 0:
                if buf[:i]:
                    out.append(("reasoning", buf[:i]))
                self.state, self.held, self.lead = "pass", "", True
                text = buf[i + len(self.close):]
            else:
                keep = next((k for k in range(min(len(self.close) - 1, len(buf)), 0, -1) if self.close.startswith(buf[-k:].lower())), 0)
                self.held = buf[len(buf) - keep:]
                if buf[:len(buf) - keep]:
                    out.append(("reasoning", buf[:len(buf) - keep]))
                return out
        if self.state == "pass":
            if self.lead:
                text = text.lstrip("\r\n")
                self.lead = not text
            if text:
                out.append(("delta", text))
        return out

    def flush(self) -> list[tuple[str, str]]:
        held, self.held = self.held, ""
        if not held:
            return []
        return [("reasoning" if self.state == "think" else "delta", held)]


def strip_think(text: str) -> str:
    """`text` without a leading think block; '' when the block never closes."""
    m = _THINK_BLOCK.match(text)
    if m:
        return text[m.end():]
    s = text.lstrip().lower()
    return "" if any(s.startswith(t) for t in _THINK_OPEN) else text


async def _close_when(cancel: asyncio.Event, response: httpx.Response) -> None:
    """Drop the provider socket when stop or steer fires, so the read is not stuck until the next token."""
    await cancel.wait()
    await response.aclose()


async def stream_chat(
    settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, kind: str = "chat",
    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream a chat completion.

    Yields {"type": "delta", "text": str} for content, {"type": "reasoning", "text": str} for a
    reasoning model's chain-of-thought, and finally
    {"type": "end", "finish_reason": str|None, "tool_calls": [{"id","name","arguments"}], "usage": {...}|None,
     "usage_est": {"prompt_tokens": int, "completion_tokens": int}, "incomplete": bool}.

    While a provider failure is being retried it also yields {"type": "retry", "attempt": int, "max": int, "delay_s": float,
    "reason": "rate_limit"|"provider_error"|"connection", "status": int|None} just before each backoff, then
    {"type": "retry", "attempt": 0} once the next response arrives. Display only: consumers that do not care skip it
    (match `end` by type, never by elimination).

    `delta.text` is always a str and never contains a leading think block (that goes out as reasoning). `incomplete` is
    True when the stream ended with neither a finish_reason nor [DONE] (a dropped connection); its tool calls are cleared.

    `cancel`, once set, closes the HTTP stream immediately. The final event then has finish_reason
    "cancelled" and no tool calls, including any arguments that had only partly arrived. Stop also interrupts the wait for
    response headers and a retry backoff (the end event then carries no usage), and the same goes for the deadline.

    The `stream_deadline` context var (a time.monotonic() value) bounds the whole stream: when it passes, the stream ends the same
    way with finish_reason "timeout". A stream that goes `llmIdleSeconds` without a byte raises LLMError.
    Connect failures, 429 and 5xx, an error frame inside a 200, a drop and an empty stream are retried (with backoff,
    under the one `llmRetries` cap) until the first content, reasoning or tool-call fragment; never after. `end` carries
    `effort_dropped` (the value) when the provider rejected reasoning_effort and the request went without it.
    """
    body: dict[str, Any] = {"model": model, "messages": wire_messages(model, messages), "stream": True, "stream_options": {"include_usage": True}}
    # Only sent when the model accepts it. A missing field is not neutral: Kimi K3 reads it as max,
    # and a model that does not support the field rejects the whole request.
    wired = effort_param(model, effort, caps=caps_lookup(model), base_url=settings.get("baseUrl"))
    if wired:
        body["reasoning_effort"] = wired
    if fast and supports_service_tier(settings):
        body["service_tier"] = "priority"
    if cap := output_cap(settings, model):
        body["max_tokens"] = cap
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice
    _with_affinity(settings, body)
    calls: dict[int, dict[str, Any]] = {}
    by_index: dict[int, int] = {}  # provider `index` -> slot, re-pointed when an index is reused for a new call
    last_idx = 0
    finish: str | None = None
    usage: dict[str, Any] | None = None
    t0 = time.time()
    out_chars = 0
    reason_chars = 0
    reason_buf: list[str] = []  # the whole reasoning, handed back on `end` for the next tool round
    cancelled = timed_out = saw_done = False
    splitter = ThinkSplitter()
    known = {t.get("function", {}).get("name") for t in tools or []}
    idle = _idle_s(settings)
    deadline_at = stream_deadline.get()
    retries = _retries(settings)
    attempt = 0  # retries spent, header-level and stream-level together
    announced = False  # a retry event is on screen until the next response arrives
    sent = True  # False when Stop or the deadline ended the call before any response arrived
    dropped = ""
    # read= bounds the wait for response headers (and backs up a chunk); the per-chunk idle limit is enforced below.
    async with httpx.AsyncClient(timeout=httpx.Timeout(CONNECT_TIMEOUT_S, read=idle + 5, write=30, pool=CONNECT_TIMEOUT_S)) as client:
        while True:
            # Per-attempt state: a re-issued request must not inherit anything from the one it replaces.
            calls, by_index, last_idx = {}, {}, 0
            finish, usage = None, None
            out_chars = reason_chars = 0
            saw_done = False
            splitter = ThinkSplitter()
            emitted = False  # the first content, reasoning or tool-call fragment ends the right to retry
            fail: tuple[str, bool, str | None, str] | None = None  # (message, retryable, kind, body)
            try:
                r = cm = None
                async for sev in _send_attempts(client, settings, body, stream=True, cancel=cancel, deadline_at=deadline_at, attempt=attempt):
                    if sev["type"] == "retry":
                        announced = True
                        yield sev
                    else:
                        r, cm, attempt = sev["response"], sev["cm"], sev["attempt"]
                if announced:
                    announced = False
                    yield {"type": "retry", "attempt": 0}  # the clear: a slow first token must not keep saying "retrying"
            except _Aborted as a:
                cancelled, timed_out, sent = a.reason == "cancelled", a.reason != "cancelled", False
                break
            if wired and "reasoning_effort" not in body:
                dropped = wired
            try:
                # Closes the socket from a second task so a read blocked on the next token returns at once.
                abort = asyncio.create_task(_close_when(cancel, r)) if cancel is not None else None
                try:
                    lines = r.aiter_lines().__aiter__()
                    while True:
                        left = idle if deadline_at is None else min(idle, deadline_at - time.monotonic())
                        try:
                            line = await asyncio.wait_for(lines.__anext__(), max(left, 0.01))
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            if deadline_at is not None and time.monotonic() >= deadline_at:
                                timed_out = True
                                break
                            raise LLMError(f"The provider stopped responding: no data for {int(idle)}s mid-reply. Try again.", kind="timeout") from None
                        if cancel is not None and cancel.is_set():
                            cancelled = True
                            break
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            saw_done = True
                            break
                        try:
                            obj = json.loads(data)
                        except ValueError:
                            continue
                        if obj.get("error"):
                            err = obj["error"]
                            body_s = json.dumps({"error": err})
                            text, retryable = _frame_error(err)
                            # The frame's own code stands in for the status, so a relayed 429 is `rate_limit`, not `bad_request`.
                            kind_e = classify_error(_frame_status(err), body_s)
                            fail = (text, retryable and kind_e not in _PERMANENT_KINDS, kind_e, body_s)
                            break
                        if isinstance(obj.get("usage"), dict):
                            usage = parse_usage(obj["usage"])
                        choice = (obj.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        reason = _reason_text(delta)
                        if reason:
                            emitted = True
                            reason_chars += len(reason)
                            reason_buf.append(reason)
                            yield {"type": "reasoning", "text": reason}
                        for kind_p, piece in splitter.feed(_content_text(delta)):
                            emitted = emitted or bool(piece)
                            if kind_p == "reasoning":
                                reason_chars += len(piece)
                                reason_buf.append(piece)
                            else:
                                out_chars += len(piece)
                            yield {"type": kind_p, "text": piece}
                        for tc in delta.get("tool_calls") or []:
                            emitted = True
                            last_idx = _add_fragment(calls, by_index, tc, last_idx, known)
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if cancel is None or not cancel.is_set():
                        if not isinstance(e, httpx.HTTPError):
                            raise
                        fail = (describe_transport_error(e), True, "transport", "")
                    else:
                        cancelled = True
                finally:
                    if abort is not None:
                        abort.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await abort
            finally:
                await _close_cm(cm)
            if cancel is not None and cancel.is_set():
                cancelled, fail = True, None
            if fail is None and not (cancelled or timed_out) and not emitted and finish is None and usage is None:
                fail = ("The provider returned an empty reply. Try again, or pick another model.", True, "server", "")
            if fail is None or cancelled or timed_out:
                break
            message, retryable, kind_f, body_f = fail
            delay = retry_delay(attempt + 1)
            if not emitted and retryable and attempt < retries and (deadline_at is None or time.monotonic() + delay < deadline_at):
                attempt += 1
                log.warning("provider failed before the first token (%s); retry %d/%d in %.1fs", message, attempt, retries, delay)
                announced = True
                yield _retry_event(attempt, retries, delay, "connection" if kind_f == "transport" else "provider_error", None)
                if not await _backoff(delay, cancel):
                    cancelled = True
                    break
                continue
            # Nothing is re-issued once a token reached the caller: the reply stays what it was and says it was cut off.
            message += (f" Retried {attempt} time{'s' if attempt != 1 else ''}." if attempt else "") + (" The reply was cut off." if emitted else "")
            raise _provider_error(message, kind_f, None, body_f)
    for kind_p, piece in splitter.flush():  # a tag-like tail held back at the end of the stream
        if kind_p == "reasoning":
            reason_chars += len(piece)
            reason_buf.append(piece)
        else:
            out_chars += len(piece)
        yield {"type": kind_p, "text": piece}
    incomplete = finish is None and not saw_done and not cancelled and not timed_out and not (cancel is not None and cancel.is_set())
    if cancelled or (cancel is not None and cancel.is_set()):
        # A half-parsed tool call must not run. The caller keeps whatever text already streamed.
        calls = {}
        finish = "cancelled"
    elif timed_out:
        calls = {}
        finish = "timeout"
    if incomplete:  # the connection died mid-reply: a half-received tool call must not run
        calls = {}
    tool_calls = _finish_calls(calls)
    # Reasoning is billed as completion tokens, so it counts toward cost and the run meter. A call Stop or the deadline
    # ended before any response is not billed at all: the estimate would count a prompt nobody answered.
    p_chars, c_chars = (len(json.dumps(messages)) if sent else 0), out_chars + reason_chars + sum(len(c["arguments"]) for c in tool_calls)
    if sent:
        _emit_usage(model, kind, usage, int((time.time() - t0) * 1000), p_chars, c_chars)
    # usage_est is always present: this route often omits `usage` on streamed replies, and the meter cannot run on None.
    end: dict[str, Any] = {"type": "end", "finish_reason": finish, "tool_calls": tool_calls, "usage": usage,
                           "usage_est": {"prompt_tokens": p_chars // 4, "completion_tokens": c_chars // 4}, "incomplete": incomplete}
    if dropped:
        end["effort_dropped"] = dropped
    if reason_buf:
        end["reasoning"] = "".join(reason_buf)
    yield end


def _is_other_call(cur: dict[str, Any], tc: dict[str, Any]) -> bool:
    """A fragment with a new provider id and a function name is a new call, not more of `cur`. An id we invented
    ourselves (`pid` unset) was never the provider's, so a real one arriving later is not a new call."""
    cid = tc.get("id")
    return bool(cid and cur["name"] and cur["pid"] and cur["pid"] != cid and (tc.get("function") or {}).get("name"))


def _slot(calls: dict[int, dict[str, Any]], by_index: dict[int, int], tc: dict[str, Any], last: int) -> int:
    """Which call a streamed tool-call fragment belongs to.

    Providers usually send an int `index`, but some send null or omit it on parallel calls. Without one,
    a fragment carrying a new `id` opens a new call, as does a fresh name arriving after the latest call's
    arguments; anything else continues the latest. An indexed fragment whose id differs from a named call
    already in that slot is also a new call: `by_index` is re-pointed at it, so the rest of its fragments
    (which carry the same index and usually no id) follow it there.
    """
    try:
        idx = int(tc["index"]) if tc.get("index") is not None else None
    except (TypeError, ValueError):
        idx = None
    cid = tc.get("id")
    fn = tc.get("function") or {}
    if idx is None:
        if not calls:
            return 0
        cur = calls[last]
        if (cid and cur["name"] and cid != cur["id"] and cur["pid"]) or (not cid and fn.get("name") and cur["name"] and cur["arguments"]):
            return max(calls) + 1
        return last
    slot = by_index.get(idx)
    if slot is None:
        slot = idx if idx not in calls else max(calls) + 1
    elif _is_other_call(calls[slot], tc):
        slot = max(calls) + 1
    by_index[idx] = slot
    return slot


def _add_fragment(calls: dict[int, dict[str, Any]], by_index: dict[int, int], tc: dict[str, Any], last: int, known: set[Any]) -> int:
    """Fold one streamed tool-call fragment into `calls`; returns the slot it landed in."""
    idx = _slot(calls, by_index, tc, last)
    cur = calls.setdefault(idx, {"id": "call_" + uuid.uuid4().hex[:12], "pid": None, "name": "", "arguments": ""})
    if tc.get("id"):
        cur["id"] = cur["pid"] = tc["id"]
    fn = tc.get("function") or {}
    frag = fn.get("name")
    if frag and isinstance(frag, str):
        name = cur["name"]
        # Some providers resend the whole name on every chunk; a real split name still joins.
        resent = frag == name or (name in known and not any(k and k.startswith(name + frag) for k in known))
        if not resent:
            cur["name"] = name + frag
    if fn.get("arguments"):
        cur["arguments"] += fn["arguments"]
    return idx


def _finish_calls(calls: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Calls in creation order with the public shape; nameless ones (they cannot run) are dropped and repeated
    provider ids made unique, since approvals and plan steps are keyed on the id."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i in sorted(calls):
        c = calls[i]
        if not c["name"]:
            continue
        cid, n = c["id"], 1
        while cid in seen:
            n += 1
            cid = f"{c['id']}_{n}"
        seen.add(cid)
        out.append({"id": cid, "name": c["name"], "arguments": c["arguments"]})
    return out


async def complete(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], kind: str = "other", *,
                   effort: str = "default", cancel: asyncio.Event | None = None, deadline: float | None = None) -> str:
    """Non-streaming completion (used for extraction, and for vision: `content` may be a list of text/image_url parts).

    `cancel` (Stop) and `deadline` (a time.monotonic() value) end the call early as an LLMError of kind 'cancelled' /
    'timeout'; neither is read from a context var, so a call made after a run's deadline has passed is unaffected.
    """
    t0 = time.time()
    body: dict[str, Any] = {"model": model, "messages": wire_messages(model, messages), "stream": False}
    wired = effort_param(model, effort, caps=caps_lookup(model), base_url=settings.get("baseUrl"))
    if wired:
        body["reasoning_effort"] = wired
    if cap := output_cap(settings, model):
        body["max_tokens"] = cap
    _with_affinity(settings, body)
    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=CONNECT_TIMEOUT_S)) as client:
        try:
            r, _, _ = await _send_with_retry(client, settings, body, stream=False, cancel=cancel, deadline_at=deadline)
        except _Aborted as a:
            if a.reason == "cancelled":
                raise LLMError("Stopped.", kind="cancelled") from None
            raise LLMError("The model provider took too long.", kind="timeout") from None
    data = r.json()
    text = strip_think(_content_text(data["choices"][0]["message"]))
    _emit_usage(model, kind, parse_usage(data["usage"]) if isinstance(data.get("usage"), dict) else None, int((time.time() - t0) * 1000), _prompt_chars(messages), len(text))
    return text


def audio_usage(model: str, seconds: float, kind: str = "voice-stt") -> None:
    """Record transcribed audio in the usage log; duration_ms carries the audio length, not wall time.

    Synchronous and silent on purpose: transcription runs on worker threads, and a missing
    accounting row must never fail a transcription.
    """
    try:
        _emit_usage(model, kind, None, int(seconds * 1000), 0, 0)
    except Exception:  # noqa: BLE001 - accounting must never break a transcription
        pass
