"""Provider-agnostic LLM access over any OpenAI-compatible API (a LiteLLM proxy is just one of them)."""
from __future__ import annotations

import asyncio
import contextlib
import email.utils
import ipaddress
import json
import logging
import random
import re
import time
import uuid
import weakref
from contextvars import ContextVar
from typing import Any, AsyncIterator, Callable
from urllib.parse import urlparse

import httpx

from . import anthropic_native, auth_breaker, provider_route, providers, repos, runtime
from .extensions.hooks import HookBus
from .runtime import checked
from .runtime.protocols import ModelReply, ModelRequest
from .runtime.slots import Refused, at_least_as_private
from .limits import (LOOP_DEFAULTS, VOLATILE_HEADER, CODING_SESSION_MAX_CONCURRENT, COMPACT_AT, COMPACT_AT_TOKENS, COMPACT_KEEP_RECENT, COMPACT_KEEP_TOKENS, DELEGATION_AFTER_ROUNDS, DESK_PARK_AFTER_SECONDS, GMAIL_SEND_HOLD_SECONDS, TELEGRAM_LONG_RUN_MINUTES, LLM_IDLE_SECONDS, LLM_RETRIES, MICRO_AT, MICRO_KEEP, RETRIEVAL_CANDIDATES, RETRIEVAL_MIN_SIMILARITY, RETRIEVAL_PER_DOC_CAP, TOOL_DEFER_ABOVE, WORKER_MAX_CONCURRENT)
from .permissions import DEFAULTS as PERMISSION_DEFAULTS
log = logging.getLogger("personal_os.llm")

# model.request and model.response fire on this bus (app.py points it at the extension host's). Usage accounting is
# its subscriber to model.response; callers that know the chat/project set usage_context.
bus: HookBus = HookBus()
usage_context: ContextVar[dict[str, Any]] = ContextVar("usage_context", default={})
# Absolute time.monotonic() by which the current stream must be over; set only around a closing-answer call (a hang
# bound, not a reply cap). A context var rather than a parameter so every caller of stream_chat keeps its signature.
stream_deadline: ContextVar[float | None] = ContextVar("stream_deadline", default=None)
# Requests that share a prompt prefix (one chat's turns, sibling workers of one kind) carry the same id, so Fireworks
# routes them to the replica that holds that prefix in its cache. Set by the chat loop and the worker loop.
session_affinity: ContextVar[str | None] = ContextVar("session_affinity", default=None)


# Views the model-facing side honours: a view in `hiddenViews` is off, so its tools, loops and prompt data stand down.
# `spaces` is the canvas view's key (the sidebar row it hides is Canvases). propose_times_draft reads the calendar and
# writes a Gmail draft, so either view off takes it away. todo_write is the working checklist, not Lists.
TOOL_VIEWS = (("health_", "health"), ("todo_list", "todos"), ("todo_add", "todos"), ("todo_update", "todos"),
              ("todo_delete", "todos"), ("google_tasks_", "todos"), ("calendar_", "calendar"), ("meeting_brief", "calendar"),
              ("propose_times_draft", "calendar"), ("propose_times_draft", "mail"), ("schedule_suggest", "calendar"),
              ("gmail_", "mail"), ("mail_followups", "mail"), ("space_", "spaces"))


def view_on(settings: dict[str, Any], view: str) -> bool:
    return view not in (settings.get("hiddenViews") or ())


def tool_views(name: str) -> list[str]:
    return [v for p, v in TOOL_VIEWS if name.startswith(p)]


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
    bus.emit_sync("model.response", rec)


def _emit_request(model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> None:
    if bus.active("model.request"):
        bus.emit_sync("model.request", {"model": model, "message_count": len(messages),
                                        "tool_names": [n for t in tools or [] if (n := (t.get("function") or {}).get("name"))],
                                        "tokens_est": _prompt_chars(messages, tools) // 4})


DEFAULT_SETTINGS: dict[str, Any] = {
    # Empty until onboarding (or an upgrade from a stored baseUrl). Nothing here is a model alias that only
    # one provider knows: an unsaved defaultModel is filled per provider (providers.default_model, in app.settings()).
    "baseUrl": "",
    "apiKey": "",
    "defaultModel": "",
    # Preset id from providers.py, or None to infer it from baseUrl. onboardedAt is the ISO time setup finished.
    "provider": None,
    # The configured providers: [{id, kind, name, baseUrl, models?}]. Read-only over PUT /settings (POST /providers owns it);
    # keys live in the providerKeys secret. A model is "<provider id>/<model>"; see provider_route.resolve.
    "providers": [],
    "onboardedAt": None,
    "systemPrompt": (
        "You are the assistant inside the user's personal AI OS. Be direct, concise and useful. Context (memories, graph, "
        "file excerpts) may be provided; use it when relevant and don't mention it unless asked."
    ),
    # Model tiers (providers.tier_model): "" = the active provider's default for that tier.
    "modelHigh": "",
    "modelMedium": "",
    "modelLow": "",
    "defaultEffort": repos.DEFAULT_EFFORT,  # the reasoning effort a new chat starts on (a chat's own choice wins)
    # The global runtime slot selections ({slot: provider id}, tools.source {enable, disable}) and the sandbox dimensions the user
    # accepted a backend to enforce only partly; written only through PUT /runtime (runtime/routes.py).
    "runtime": {},
    "workerEffort": "medium",  # reasoning effort of workers' rounds: low | medium | high, or "chat" to follow the chat's
    "workerModel": "",  # "" = workers run on the chat model; an agent definition's own model still wins
    "autoRoute": False,  # new chats start on Auto: the fast or the default model per message
    "autoLearn": True,
    "followUps": True,  # up to 3 suggested next questions under the latest reply (uses the extraction model)
    "autoTitle": True,  # a short model-written chat title after the first reply (uses the extraction model)
    # Pre-image copies of local files the agent overwrites or moves, so Undo works (filesnap.py).
    "fileSnapshots": True,
    # Every permission key (tools, alwaysAsk, permissionRules, ...) and its default: permissions.py.
    **PERMISSION_DEFAULTS,
    # loop lane: agent-loop tunables (limits.LOOP_DEFAULTS has the defaults, limits.RANGES the bounds)
    **LOOP_DEFAULTS,
    "stuckSensitivity": "standard",  # off | relaxed | standard | strict: how soon the stuck detector steps in (stuck.limits_for)
    "parallelReads": 0,  # read-only calls of one round that run together; 0 = automatic (limits.worker_slots), 1 = one at a time
    # codemode lane: run_python(tools=...) lets a script drive app tools (toolbridge.py).
    "codeMode": True,  # off: run_python refuses `tools`; scripts cannot call app tools
    "codeModeMaxCalls": 100,  # inner tool calls one script may make (1..500)
    # Bank long messages the user writes as style samples and keep their voice profile current (style.py).
    # Independent of autoLearn: wanting the app to learn facts is not the same as wanting it to copy your voice.
    "learnStyle": True,
    "theme": "dark",
    "accent": "sage",
    "accentCustom": "#7c8cf0",  # the colour behind accent "custom"
    "simpleMode": False,  # hide models, permission detail, context, developer settings and chat internals (see lib/simpleMode.ts)
    "mascot": "grain",  # which creature draws the faces: grain, blobs, off (see lib/appearance.ts)
    "uiFont": "system",  # interface typeface: system, serif, mono (see lib/appearance.ts)
    "codeFont": "default",  # code typeface: default, menlo
    "gatherShortcut": "Control+Alt+Command+Space",
    # Global quick capture: a small window that appends a timestamped bullet to today's daily note.
    "quickCaptureShortcut": "CommandOrControl+Shift+Space",
    "quickAskShortcut": "Alt+Space",
    # Hold this in the chat box to dictate while held; a quick tap latches it on.
    "dictationChord": "Control+Alt+D",
    # Shell modularity: Today-screen cards ({key: bool}, missing = shown) and sidebar views the user removed.
    "homeWidgets": {},
    "hiddenViews": [],  # a fresh install starts with no extensions enabled (app._seed_hidden_modules); an install without the key keeps every view on
    "pickerModels": [],  # model ids the picker lists; [] = every keyed provider's preset models
    # Rows hidden from the sidebar only; they stay reachable everywhere else. Default: only Files, Lists, Calendar, Library, Jobs, Canvases and Projects show.
    "sidebarHidden": ["mail", "health"],
    # {view: "sidebar" | "apps"}; missing = the module's own default placement.
    # Bump when the default-off set changes so existing DBs pick up the change once.
    "modulesDefault": 6,
    "snapshotsEnabled": True,
    # Show traces, the context preview, the full system prompt and OTLP export in the UI. Traces are recorded either way.
    "devTools": False,
    # Context management (compaction.py). Window and thresholds are estimates (len//4), not provider counts.
    "contextWindow": 0,  # 0 = automatic: the proxy's figure for the model, else limits.CONTEXT_WINDOW_FALLBACK; non-zero overrides
    "autoCompact": True,
    "compactAt": COMPACT_AT,
    "compactKeepRecent": COMPACT_KEEP_RECENT,
    "compactAtTokens": COMPACT_AT_TOKENS,  # absolute trigger on history alone, for windows too large for the fraction to ever fire
    "compactKeepTokens": COMPACT_KEEP_TOKENS,
    "microKeep": MICRO_KEEP,
    "microAt": MICRO_AT,  # old tool results stub out past a quarter of the window: past ~30k tokens a round, time to first token dominates
    # Opt-in OpenTelemetry GenAI export (otel_export.py). Off by default; replaced whole through PUT /settings.
    # Loopback endpoints only unless allowRemote; no message content unless includeContent.
    "otelExport": {"enabled": False, "endpoint": "", "headers": {}, "includeContent": False, "allowRemote": False, "timeoutSeconds": 5},
    # Offer MCP tools through mcp_tool_search once more than this many are ready (0 = always send every schema).
    # Past this many built-in tools, offer the core set plus tool_search instead of every schema (0 = send them all).
    "toolDeferAbove": TOOL_DEFER_ABOVE,
    # Put the notes each connected MCP server sends at initialize into the prompt (fenced, scanned, capped).
    "mcpServerNotes": True,
    # Provider resilience (retry/backoff section below). Retries only happen before a reply's first token;
    # llmIdleSeconds is how long a stream may go without a byte before it is abandoned with a clear error.
    "llmRetries": LLM_RETRIES,
    "llmIdleSeconds": LLM_IDLE_SECONDS,  # a reasoning model can think a long while before its first token
    # Chats and inbox messages whose last activity is older than this many days are archived (restorable). 0 = off.
    "autoArchiveDays": 0,
    # Cowork desks. deskMaxLive bounds how many desks may be running at once.
    # How long a desk waits on a card nobody is watching before letting the run go. The card stays
    # pending and decidable; only the run lets go. 0 = wait forever, which is what a chat does.
    "parkAfterSeconds": DESK_PARK_AFTER_SECONDS,
    # A plan the model rates high-confidence goes ahead this many seconds after its card opens unless the user answers
    # or touches it first. 0 = off. Never in manual permission mode, never after untrusted content.
    "planAutoApproveSeconds": 30,
    "deskMaxLive": 0,  # 0 = automatic (limits.worker_slots); a non-zero value overrides
    # Relaunch desks a restart interrupted mid-turn. Off by default: a desk with a call whose outcome is
    # unknown, or one waiting on an approval or its plan, is never relaunched either way.
    "deskAutoResume": False,
    # A new chat's first message starts it as a task (a desk) that works through its steps; a plain question is answered
    # and the desk settles done. The composer's Autonomous switch starts from this value.
    "autonomousByDefault": True,
    # Subagents (subagents.py): how many may run at once across the app.
    "subagentMaxConcurrent": 0,  # 0 = automatic (limits.worker_slots); a non-zero value overrides
    # Workers (workers.py): the chat's front agent hands multi-step work to detached background workers on the chat's own
    # model. delegationForce routes (never stops) work: after delegationAfterRounds rounds of tool calls in one reply the
    # reply may only delegate and answer. workerMaxConcurrent workers run at once; the rest queue in order.
    "delegationForce": True,
    "delegationAfterRounds": DELEGATION_AFTER_ROUNDS,
    "workerMaxConcurrent": WORKER_MAX_CONCURRENT,
    # OS notification when an unattended job fails, is paused, or leaves proposals (only while the app is hidden).
    "notifyJobs": True,
    # A system notification when a desk needs you or finishes, while the window is not focused.
    "deskNotify": True,
    "chatNotify": True,
    # A floating Explain / Summarize / Verify / Ask bubble over selected text.
    "selectionToolbar": True,
    # Interface zoom, percent (80-160 in steps of 5); every window applies it as its page zoom factor.
    "uiZoom": 110,
    # Default type for Files ({font: serif|sans|mono|book, size: px, measure: ch}); a doc can override it (docs.typography).
    "docTypography": {},
    "responseStyle": "default",  # what a new chat starts on; see style_presets
    "responseStyleText": "",
    # Undo window on outgoing mail (extensions/mail/outbox.py). `seconds` is clamped to 60-120 on read.
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
    # github_search/github_read; empty = the gh CLI's login (`gh auth token`), else unauthenticated (60 requests/h).
    "githubToken": "",
    "codingSessionMaxConcurrent": CODING_SESSION_MAX_CONCURRENT,    # live coding sessions at once (own pool)
    # The model view_image sends pictures to. Empty = the chat model, when the provider says it reads images.
    "visionModel": "",
    # The model generate_image calls (POST /images/generations, OpenAI shape). Empty = the tool says it is not set up.
    "imageModel": "",
    # Extra packages installed into the shared work environment (envs.py) beside its base set.
    "workEnvPackages": [],
    # {model: {"input": $/M tokens, "output": $/M tokens}} overrides for cost accounting (proxy prices are used otherwise)
    "modelPrices": {},
    "googleClientId": "",
    "googleClientSecret": "",
    "googleToken": {},
    # Google Tasks <-> todos sync. Shape and defaults live in gtasks.DEFAULT_CONFIG; patched
    # through /integrations/google/tasks-sync rather than /settings for the same reason.
    # Empty on purpose: anything named here would override that module's defaults.
    "googleTasksSync": {},
    # Names this computer's Drive backup folder ("Grain Backup (<label>)"); empty = the hostname. See drive_backup.py.
    "driveBackupLabel": "",
    # Document and memory retrieval (retrieval.py, memory_index.py). 'bm25' forces keyword-only; hybrid fuses keywords,
    # embeddings, recency and graph, and falls back to keywords when the embedding route is unavailable. The floor only drops vector-only hits (exact keyword hits survive).
    "retrievalMode": "hybrid",
    "embeddingModel": "",  # "" = keywords only until a keyed provider serves one (app.ensure_embedding_model picks it)
    "retrievalMinSimilarity": RETRIEVAL_MIN_SIMILARITY,
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
    # Workspace map (context_map.py): a short list of what exists, one switch per section. mapWorkers adds the stable part to
    # the prompt of background workers and subagents that can call context_map (off: their prompts stay small).
    "mapMemory": True, "mapFiles": True, "mapUploads": True, "mapProjects": True, "mapSkills": True,
    "mapSources": True, "mapToday": True, "mapWorkers": False,
    # Reply tracker (extensions/mail/watch.py); MailExtension.config() merges stored values over these defaults.
    "mailWatch": {"awaitingAfterDays": 3, "needsReplyAfterHours": 24, "useLLM": False,
                  "query": "newer_than:14d -category:promotions -category:social"},
    # Text Grain from your own phone over a Telegram bot (telegram.py). Off until a token is saved and a chat is paired.
    "telegramEnabled": False,
    "telegramNotifyLongRuns": False,  # also send approvals and finish notices for runs that were not started from Telegram
    "telegramLongRunMinutes": TELEGRAM_LONG_RUN_MINUTES,
    "telegramPushWorkerResults": False,  # also text the owner the reply Grain writes when a background worker finishes
    # Todo time-block planner (planner.py); PlannerModule.config() merges stored values over these.
    "planner": {"workStart": "09:00", "workEnd": "17:30", "workDays": [1, 2, 3, 4, 5], "bufferMin": 10, "minBlockMin": 15,
                "maxBlockMin": 120, "slotStepMin": 15, "lookaheadDays": 7, "calendarName": "Grain Todos",
                "busyCalendarIds": []},
    # ext lane: where the extensions catalog index (a JSON list of installable extensions) is fetched from.
    "extensionCatalogUrl": "https://raw.githubusercontent.com/natejly/personal-os/main/examples/extensions/catalog.json",
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


NOT_CONFIGURED = "No AI provider is set up yet. Click Finish setup above the chat box (or Settings → Model)."


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


def supports_parallel_tool_calls(settings: dict[str, Any]) -> bool:
    """Providers known to take `parallel_tool_calls`; elsewhere (and for an unrecognised host) the field is not sent."""
    return providers.effective(settings) in ("fireworks", "openai", "litellm", "custom", "openrouter")
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
# How long a stream may wait for response headers. A hosted provider answers with headers at once and thinks afterwards,
# so a silent header wait is a stuck replica: retry it long before the between-chunk idle limit. A local server (Ollama)
# may hold its headers while it loads the model or reads a long prompt, and a LiteLLM proxy may hold them until the
# upstream's first chunk (minutes for a model that thinks without streaming it), so those keep the idle limit.
FIRST_BYTE_S = 60.0

_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[Any, httpx.AsyncClient]]" = weakref.WeakKeyDictionary()


def _client() -> httpx.AsyncClient:
    """One keep-alive client per event loop, so a tool loop does not pay a TCP and TLS handshake per round; timeouts go
    per request. Rebuilt when httpx.AsyncClient was swapped (tests put in a mock transport) or the client was closed."""
    loop = asyncio.get_running_loop()
    made = _clients.get(loop)
    if made is None or made[0] is not httpx.AsyncClient or made[1].is_closed:
        made = _clients[loop] = (httpx.AsyncClient, httpx.AsyncClient(timeout=httpx.Timeout(CONNECT_TIMEOUT_S, read=DEFAULT_IDLE_S + 5, write=30)))
    return made[1]


async def aclose() -> None:
    """Close this loop's shared client (app shutdown)."""
    made = _clients.pop(asyncio.get_running_loop(), None)
    if made is not None:
        await made[1].aclose()


def _first_byte_s(settings: dict[str, Any]) -> float | None:
    """The header-wait limit for a stream, or None to leave it to the idle limit (a local server or a proxy)."""
    return None if providers.effective(settings) in ("ollama", "custom", "litellm") else min(FIRST_BYTE_S, _idle_s(settings))


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
    if kind == "unsupported_param" and re.search(r"does not support (tools|tool[ _]?(calling|use)|function)", detail, re.I):
        return f"This model cannot call tools ({status}), and chats and agents send them every turn. Pick a model that supports tool calling. {detail}".strip()
    if kind == "unsupported_param" and re.search(r"does not support (images?|vision|image input)", detail, re.I):
        return f"This model cannot read images ({status}). Remove the picture or pick a model that reads images. {detail}".strip()
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


async def _race(aw: Any, cancel: asyncio.Event | None, deadline_at: float | None, limit: float | None = None) -> Any:
    """Await `aw`, but give up the moment `cancel` is set or `deadline_at` (a time.monotonic() value) passes. `limit`
    seconds without an answer raise httpx.ReadTimeout instead: a silence worth retrying, not a Stop or the deadline."""
    task = asyncio.ensure_future(aw)
    waiter = asyncio.ensure_future(cancel.wait()) if cancel is not None else None
    left = None if deadline_at is None else max(deadline_at - time.monotonic(), 0.0)
    by_limit = limit is not None and (left is None or limit < left)
    if by_limit:
        left = limit
    try:
        await asyncio.wait({task, *([waiter] if waiter is not None else [])}, timeout=left, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            return task.result()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        if by_limit and not (cancel is not None and cancel.is_set()):
            raise httpx.ReadTimeout(f"no response headers in {limit:g}s")
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


# Optional fields ('service_tier', 'user') a provider rejected for a model, as 'base|model|field'. In memory only: a
# restart costs one failed request per field, and a provider that starts accepting one is picked up again.
_rejected_fields: set[str] = set()


def _field_ok(settings: dict[str, Any], model: Any, field: str) -> bool:
    return f"{_caps_key(settings.get('baseUrl'), str(model or ''))}|{field}" not in _rejected_fields


_EFFORT_MSG = ("reasoning_effort", "output_config", "reasoning effort", "does not support thinking", "does not support reasoning")


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
    if "parallel_tool_calls" in body and (param == "parallel_tool_calls" or "parallel_tool_calls" in msg):
        return "parallel_tool_calls"
    if "user" in body and param == "user":  # the affinity id is an optimisation, never worth a failed reply
        return "user"
    return None


def _tools_need_no_effort(status: int, text: str, body: dict[str, Any]) -> bool:
    """A 400 saying chat completions take function tools only with reasoning_effort 'none' (OpenAI's gpt-6 models)."""
    if status != 400 or not body.get("tools") or body.get("reasoning_effort") == "none":
        return False
    msg = _error_fields(text)[2]
    return "function tools" in msg and "reasoning_effort" in msg


def tools_effort(settings: dict[str, Any], model: str) -> str | None:
    """'none' for a model that takes function tools on chat completions only without reasoning (see above), else None."""
    return "none" if providers.effective(settings) == "openai" and _model_slug(model).startswith("gpt-6") else None


async def _close_cm(cm: Any) -> None:
    if cm is not None:
        with contextlib.suppress(Exception):
            await cm.__aexit__(None, None, None)


def _retry_event(attempt: int, retries: int, delay: float, reason: str, status: int | None) -> dict[str, Any]:
    """The `retry` event announcing a backoff that is about to start (display only; nothing reads it back)."""
    return {"type": "retry", "attempt": attempt, "max": retries, "delay_s": delay, "reason": reason, "status": status}


async def _send_attempts(client: httpx.AsyncClient, settings: dict[str, Any], body: dict[str, Any], *, stream: bool,
                         cancel: asyncio.Event | None = None, deadline_at: float | None = None,
                         attempt: int = 0, timeout: httpx.Timeout | None = None,
                         first_byte: float | None = None) -> AsyncIterator[dict[str, Any]]:
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

    `timeout` is the per-request httpx timeout. `first_byte` bounds a stream's wait for response headers; a header
    wait that runs out is retried without the session-affinity id, so the retry can land on another replica. A
    non-streamed call whose answer times out is not retried: the provider is still generating, and billing, it.
    """
    retries = _retries(settings)
    native = anthropic_native.is_native(settings)  # the OpenAI-compatible endpoint drops cache_control: use the Messages API
    url = _url(settings, "/messages" if native else "/chat/completions", body.get("model"))
    if (held := auth_breaker.check(settings)) is not None:
        raise LLMError(held, kind="auth")
    stepped = dropped = no_affinity = False
    sent_effort = body.get("reasoning_effort")
    kw: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
    while True:
        if cancel is not None and cancel.is_set():
            raise _Aborted("cancelled")
        hdrs, payload = (anthropic_native.headers(settings), anthropic_native.to_request(body)) if native else (_headers(settings), body)
        if no_affinity:
            hdrs.pop("x-session-affinity", None)
        cm = client.stream("POST", url, headers=hdrs, json=payload, **kw) if stream else None
        try:
            if cm is not None:
                r = await _race(cm.__aenter__(), cancel, deadline_at, first_byte)
                if r.status_code >= 400:
                    await _race(r.aread(), cancel, deadline_at)
            else:
                r = await _race(client.post(url, headers=hdrs, json=payload, **kw), cancel, deadline_at)
        except _Aborted:
            await _close_cm(cm)
            auth_breaker.inconclusive(settings)
            raise
        except (httpx.TransportError, httpx.ProtocolError) as e:
            await _close_cm(cm)
            auth_breaker.inconclusive(settings)
            if cancel is not None and cancel.is_set():
                raise _Aborted("cancelled") from e
            if isinstance(e, httpx.ReadTimeout):
                if not stream:
                    raise LLMError("The model provider took too long to answer. Try again, or pick a faster model.", kind="timeout") from e
                no_affinity = True
                body.pop("user", None)
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
        if _tools_need_no_effort(r.status_code, r.text, body):
            body["reasoning_effort"] = "none"  # omitting it is not enough: the model's own default effort also refuses tools
            log.warning("provider refuses function tools with reasoning_effort; resending with none")
            continue
        field = _rejected_optional(r.status_code, r.text, body)
        if field == "reasoning_effort" and body[field] in ("xhigh", "max") and not stepped:
            stepped = True
            body[field] = "high"
            log.warning("provider rejected reasoning_effort %s; resending with high", sent_effort)
            continue
        if field:
            dropped = dropped or field == "reasoning_effort"
            body.pop(field, None)
            if field != "reasoning_effort":
                _rejected_fields.add(f"{_caps_key(settings.get('baseUrl'), str(body.get('model') or ''))}|{field}")
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
        if kind == "auth" and (up := auth_breaker.upstream(settings, r.text)):
            raise LLMError(f"The proxy's upstream key for {up} was rejected ({r.status_code}). Fix it in the proxy's config or "
                           f"environment, not in Settings. {_provider_message(r.text)}".strip(), kind, r.status_code)
        raise _provider_error(describe_http_error(r.status_code, r.reason_phrase, r.text, attempt, retry_after, kind), kind, r.status_code, r.text)


async def _send_with_retry(client: httpx.AsyncClient, settings: dict[str, Any], body: dict[str, Any], *, stream: bool,
                           cancel: asyncio.Event | None = None, deadline_at: float | None = None,
                           attempt: int = 0, timeout: httpx.Timeout | None = None) -> tuple[httpx.Response, Any, int]:
    """`_send_attempts` drained: (response, stream context or None, retries used), for callers with no use for the retry events."""
    async for ev in _send_attempts(client, settings, body, stream=stream, cancel=cancel, deadline_at=deadline_at, attempt=attempt, timeout=timeout):
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
    if (sid := session_affinity.get()) and _affinity_ok(settings) and _field_ok(settings, body.get("model"), "user"):
        body["user"] = sid
    return body


# Ids that name a non-chat model when the proxy reports no `mode` for them: a conservative
# pattern, since the picker still shows whatever model the chat already uses.
_NON_CHAT_ID = ((re.compile(r"rerank", re.I), "rerank"), (re.compile(r"embed", re.I), "embedding"),
                (re.compile(r"dall-e|gpt-image|imagen|flux|stable-diffusion|sdxl", re.I), "image_generation"), (re.compile(r"whisper|tts", re.I), "audio"),
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


IMAGE_PROMPT_CHARS = 4_000  # about 1,000 tokens a picture, at the 4-characters-a-token estimate


def _prompt_chars(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> int:
    """Characters of prompt for the usage estimate, tool schemas included. An image part counts IMAGE_PROMPT_CHARS, not
    its length: a base64 picture is megabytes of characters but around a thousand tokens. The provider's own usage
    figure replaces this estimate when it sends one."""
    images = 0
    slim = []
    for m in messages:
        if isinstance(m.get("content"), list):
            parts = [p for p in m["content"] if not (isinstance(p, dict) and p.get("type") == "image_url")]
            images += len(m["content"]) - len(parts)
            m = {**m, "content": parts}
        slim.append(m)
    return len(json.dumps(slim)) + images * IMAGE_PROMPT_CHARS + (len(json.dumps(tools)) if tools else 0)


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


ANTHROPIC_FALLBACK_CAP = 64_000  # when the Models API cap is unknown (e.g. a routed call while another provider is active)


def requires_max_tokens(settings: dict[str, Any]) -> bool:
    """Only the Anthropic API refuses a request without max_tokens. Nothing else gets the field."""
    return providers.effective(settings) == "anthropic"


def output_cap(settings: dict[str, Any], model: str) -> int | None:
    """The max_tokens to send: the model's own output maximum for a provider that requires the field, DeepSeek V4's
    ceiling (its provider default truncates thinking replies), else None."""
    if requires_max_tokens(settings):
        return int(caps_lookup(model).get("max_output_tokens") or 0) or ANTHROPIC_FALLBACK_CAP
    return DEEPSEEK_V4_MAX_TOKENS if _model_slug(model).startswith("deepseek-v4") else None


def wire_messages(model: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`messages` as sent: reasoning_content kept for a family that reads it back, dropped for any other."""
    if _model_slug(model).startswith(_REASONING_ECHO) or not any("reasoning_content" in m for m in messages):
        return messages
    return [{k: v for k, v in m.items() if k != "reasoning_content"} for m in messages]


def is_anthropic_model(model: str) -> bool:
    return "claude" in model.lower()


def with_cache_breakpoints(model: str, body: dict[str, Any]) -> dict[str, Any]:
    """Anthropic models cache only behind explicit `cache_control` marks (other providers cache a shared prefix by themselves).

    Up to four, all on content that repeats between rounds: the stable system prompt, the tool list, the message before
    the newest turn (the per-turn retrieval block, or the newest user message, follows it), and the last message, so the
    next round of a tool loop re-reads everything already sent. Mutates `body` only; the caller's messages are copied, not edited.
    """
    if not is_anthropic_model(model):
        return body
    mark = {"type": "ephemeral"}
    msgs = list(body["messages"])

    def put(i: int) -> None:
        while i >= 0:  # a tool-calling assistant message has no text to mark: walk back to one that does
            c = msgs[i].get("content")
            if isinstance(c, str) and c:
                msgs[i] = {**msgs[i], "content": [{"type": "text", "text": c, "cache_control": mark}]}
                return
            if isinstance(c, list) and c and isinstance(c[-1], dict):
                msgs[i] = {**msgs[i], "content": [*c[:-1], {**c[-1], "cache_control": mark}]}
                return
            i -= 1

    first_sys = next((i for i, m in enumerate(msgs) if m.get("role") == "system"), None)
    if first_sys is not None:
        put(first_sys)
    last = len(msgs) - 1
    newest = last if msgs[last].get("role") == "user" else next((i for i in range(last, -1, -1) if msgs[i].get("role") == "user"), last)
    if newest > 0 and msgs[newest - 1].get("role") == "system" and str(msgs[newest - 1].get("content", "")).startswith(VOLATILE_HEADER):
        newest -= 1
    if (first_sys or 0) < newest - 1:
        put(newest - 1)
    put(last)
    body["messages"] = msgs
    if tools := body.get("tools"):
        body["tools"] = [*tools[:-1], {**tools[-1], "cache_control": mark}]
    return body


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


async def _stream_openai(
    settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, kind: str = "chat",
    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream a chat completion over the OpenAI-compatible HTTP API: the built-in model client's chat body.

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
    settings, model = provider_route.resolve(settings, model)
    body: dict[str, Any] = {"model": model, "messages": wire_messages(model, messages), "stream": True, "stream_options": {"include_usage": True}}
    # Only sent when the model accepts it. A missing field is not neutral: Kimi K3 reads it as max,
    # and a model that does not support the field rejects the whole request.
    wired = effort_param(model, effort, caps=caps_lookup(model), base_url=settings.get("baseUrl"))
    if tools and (forced := tools_effort(settings, model)):
        wired = forced
    if wired:
        body["reasoning_effort"] = wired
    if fast and supports_service_tier(settings) and _field_ok(settings, model, "service_tier"):
        body["service_tier"] = "priority"
    if cap := output_cap(settings, model):
        body["max_tokens"] = cap
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice
        if supports_parallel_tool_calls(settings):
            body["parallel_tool_calls"] = True
    _with_affinity(settings, body)
    with_cache_breakpoints(model, body)
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
    guard = LeakGuard()
    known = {t.get("function", {}).get("name") for t in tools or []}
    idle = _idle_s(settings)
    deadline_at = stream_deadline.get()
    retries = _retries(settings)
    attempt = 0  # retries spent, header-level and stream-level together
    announced = False  # a retry event is on screen until the next response arrives
    sent = True  # False when Stop or the deadline ended the call before any response arrived
    dropped = ""
    p_chars = _prompt_chars(messages, tools)
    _emit_request(model, messages, tools)
    # read= backs up the header wait (first_byte) and a chunk; the per-chunk idle limit is enforced below.
    timeout = httpx.Timeout(CONNECT_TIMEOUT_S, read=idle + 5, write=30, pool=CONNECT_TIMEOUT_S)
    first_byte = _first_byte_s(settings)
    client = _client()
    while True:
        # Per-attempt state: a re-issued request must not inherit anything from the one it replaces.
        calls, by_index, last_idx = {}, {}, 0
        finish, usage = None, None
        out_chars = reason_chars = 0
        saw_done = False
        splitter = ThinkSplitter()
        guard = LeakGuard()
        emitted = False  # the first content, reasoning or tool-call fragment ends the right to retry
        fail: tuple[str, bool, str | None, str] | None = None  # (message, retryable, kind, body)
        try:
            r = cm = None
            async for sev in _send_attempts(client, settings, body, stream=True, cancel=cancel, deadline_at=deadline_at, attempt=attempt,
                                            timeout=timeout, first_byte=first_byte):
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
                lines = (anthropic_native.stream_lines(r.aiter_lines()) if anthropic_native.is_native(settings) else r.aiter_lines()).__aiter__()
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
                        where = " mid-reply" if emitted else " before replying"
                        fail = (f"The provider stopped responding: no data for {int(idle)}s{where}. Try again.", True, "timeout", "")
                        break
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
                        if kind_p == "delta" and not (piece := guard.feed(piece)):
                            continue
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
        if emitted or usage:  # the provider bills what it generated before the failure
            _emit_usage(model, kind, usage, int((time.time() - t0) * 1000), p_chars,
                        out_chars + reason_chars + sum(len(c["arguments"]) for c in calls.values()))
        raise _provider_error(message, kind_f, None, body_f)
    for kind_p, piece in splitter.flush():  # a tag-like tail held back at the end of the stream
        if kind_p == "delta":
            piece = guard.feed(piece)
            if not piece:
                continue
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
    if (tail := guard.flush()):
        out_chars += len(tail)
        yield {"type": "delta", "text": tail}
    tool_calls = _finish_calls(calls)
    leaked = 0
    if not tool_calls and not (cancelled or timed_out or incomplete):
        tool_calls = guard.calls()
        leaked = len(tool_calls)
        if leaked and finish in (None, "stop"):
            finish = "tool_calls"
    # Reasoning is billed as completion tokens, so it counts toward cost and the run meter. A call Stop or the deadline
    # ended before any response is not billed at all: the estimate would count a prompt nobody answered.
    p_chars, c_chars = (p_chars if sent else 0), out_chars + reason_chars + sum(len(c["arguments"]) for c in tool_calls)
    if sent:
        _emit_usage(model, kind, usage, int((time.time() - t0) * 1000), p_chars, c_chars)
    # usage_est is always present: this route often omits `usage` on streamed replies, and the meter cannot run on None.
    end: dict[str, Any] = {"type": "end", "finish_reason": finish, "tool_calls": tool_calls, "usage": usage,
                           "usage_est": {"prompt_tokens": p_chars // 4, "completion_tokens": c_chars // 4}, "incomplete": incomplete}
    if dropped:
        end["effort_dropped"] = dropped
    if leaked:
        end["leaked_tool_calls"] = leaked
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


_LEAK_MARKERS = ("<｜tool▁calls▁begin｜>", "<｜tool▁call▁begin｜>", "<|DSML|", "<｜DSML｜")


class LeakGuard:
    """Some DeepSeek servers print tool calls into `content` instead of `tool_calls`. Text from the first call marker on
    is held back from the answer (a marker split across chunks is held until it decides); `calls()` parses it at the end."""

    def __init__(self) -> None:
        self.raw = ""  # everything fed
        self.at = -1  # index in raw of the first marker, once seen
        self.sent = 0

    def feed(self, text: str) -> str:
        self.raw += text
        if self.at < 0:
            self.at = min((i for i in (self.raw.find(m) for m in _LEAK_MARKERS) if i >= 0), default=-1)
        if self.at >= 0:
            limit = self.at
        else:
            limit, lt = len(self.raw), self.raw.rfind("<")
            if lt >= 0 and any(m.startswith(self.raw[lt:]) for m in _LEAK_MARKERS):
                limit = lt
        out, self.sent = self.raw[self.sent:limit], max(self.sent, limit)
        return out

    def flush(self) -> str:
        if self.at >= 0:
            return ""
        out, self.sent = self.raw[self.sent:], len(self.raw)
        return out

    def calls(self) -> list[dict[str, Any]]:
        """Best-effort parse of the leaked text (V3 `<｜tool▁call▁begin｜>name<｜tool▁sep｜>{json}` and V4 `<|DSML|invoke
        name=..>` with `<|DSML|parameter name=..>` children); [] when nothing parses."""
        if self.at < 0:
            return []
        tail, out = self.raw[self.at:], []
        for m in re.finditer(r"<[|｜]DSML[|｜]invoke\s+name=\"([^\"]+)\"\s*>(.*?)(?:</[|｜]DSML[|｜]invoke>|$)", tail, re.S):
            args: dict[str, Any] = {}
            for pm in re.finditer(r"<[|｜]DSML[|｜]parameter\s+name=\"([^\"]+)\"([^>]*)>(.*?)</[|｜]DSML[|｜]parameter>", m.group(2), re.S):
                v = pm.group(3)
                if 'string="true"' not in pm.group(2):
                    with contextlib.suppress(ValueError):
                        v = json.loads(v)
                args[pm.group(1)] = v
            out.append((m.group(1), args))
        if not out:
            for m in re.finditer(r"<｜tool▁call▁begin｜>(.*?)(?:<｜tool▁call▁end｜>|$)", tail, re.S):
                body = re.sub(r"^\s*function\s*<｜tool▁sep｜>", "", m.group(1)).strip()
                name = re.split(r"<｜tool▁sep｜>|\s|`", body, 1)[0]
                a, b = body.find("{"), body.rfind("}")
                if name and 0 <= a < b:
                    with contextlib.suppress(ValueError):
                        args = json.loads(body[a:b + 1])
                        if isinstance(args, dict):
                            out.append((name, args))
        return [{"id": f"call_{uuid.uuid4().hex[:24]}", "name": n, "arguments": json.dumps(a, ensure_ascii=False)} for n, a in out]


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


async def _complete_openai(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], kind: str = "other", *,
                           effort: str = "default", cancel: asyncio.Event | None = None, deadline: float | None = None) -> str:
    """Non-streaming completion (used for extraction, and for vision: `content` may be a list of text/image_url parts).

    `cancel` (Stop) and `deadline` (a time.monotonic() value) end the call early as an LLMError of kind 'cancelled' /
    'timeout'; neither is read from a context var, so a call made after a run's deadline has passed is unaffected.
    """
    settings, model = provider_route.resolve(settings, model)
    t0 = time.time()
    body: dict[str, Any] = {"model": model, "messages": wire_messages(model, messages), "stream": False}
    wired = effort_param(model, effort, caps=caps_lookup(model), base_url=settings.get("baseUrl"))
    if wired:
        body["reasoning_effort"] = wired
    if cap := output_cap(settings, model):
        body["max_tokens"] = cap
    _with_affinity(settings, body)
    # A reasoning helper on a long input can think for minutes before it answers, so the wait is the stream idle limit.
    timeout = httpx.Timeout(CONNECT_TIMEOUT_S, read=_idle_s(settings), write=30, pool=CONNECT_TIMEOUT_S)
    with_cache_breakpoints(model, body)
    _emit_request(model, messages)
    try:
        r, _, _ = await _send_with_retry(_client(), settings, body, stream=False, cancel=cancel, deadline_at=deadline, timeout=timeout)
    except _Aborted as a:
        if a.reason == "cancelled":
            raise LLMError("Stopped.", kind="cancelled") from None
        raise LLMError("The model provider took too long.", kind="timeout") from None
    try:
        data = anthropic_native.to_response(r.json()) if anthropic_native.is_native(settings) else r.json()
    except ValueError:
        raise LLMError(f"The provider sent a reply that is not JSON ({r.status_code}). Try again.", kind="server") from None
    choices = data.get("choices") if isinstance(data, dict) else None
    if not (isinstance(choices, list) and choices and isinstance(choices[0], dict)):
        # An OpenAI-compatible gateway may answer 200 with an error object or no choices (a filtered completion).
        err = data.get("error") if isinstance(data, dict) else None
        if err:
            body_s = json.dumps({"error": err})
            raise _provider_error(_frame_error(err)[0], classify_error(_frame_status(err), body_s), None, body_s)
        raise LLMError("The provider returned an empty reply. Try again, or pick another model.", kind="server")
    text = strip_think(_content_text(choices[0].get("message") or {}))
    _emit_usage(model, kind, parse_usage(data["usage"]) if isinstance(data.get("usage"), dict) else None, int((time.time() - t0) * 1000), _prompt_chars(messages), len(text))
    return text


# ---- model.client slot (design doc section 10.7.2) ----
def _loopback_or_private(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


class OpenAICompatible:
    """core/openai-compatible: this module's HTTP client behind the model.client interface. `settings` is the app's settings()."""
    id = "core/openai-compatible"
    kinds = frozenset({"chat", "complete"})
    settings: Callable[[], dict[str, Any]] = dict

    @property
    def locality(self) -> str:
        """Where prompts go: local when every configured provider sits on this computer or the local network. A LiteLLM proxy
        forwards to models anywhere, so it is remote whatever its host."""
        cfg = self.settings()
        entries = provider_route.usable(cfg) or [{"kind": cfg.get("provider"), "baseUrl": str(cfg.get("baseUrl") or "")}]
        return "local" if all(e.get("kind") != "litellm" and e["baseUrl"] and _loopback_or_private(e["baseUrl"]) for e in entries) else "remote"

    def stream(self, req: ModelRequest) -> AsyncIterator[dict[str, Any]]:
        return _stream_openai(self.settings(), req.model, req.messages, req.tools, kind=req.kind, effort=req.effort,
                              tool_choice=req.tool_choice, fast=req.fast)

    async def complete(self, req: ModelRequest) -> ModelReply:
        return ModelReply(await _complete_openai(self.settings(), req.model, req.messages, req.kind, effort=req.effort))


default_client = OpenAICompatible()


def _alternative_client(entry: str) -> Any | None:
    """The selected non-default model client when it serves `entry` ("chat" or "complete"), else None. A selection that is gone
    while the default is less private fails the call."""
    try:
        chosen = runtime.active.alternative("model.client")
    except Refused as e:
        raise LLMError(str(e), kind="unavailable") from None
    return chosen if chosen is not None and entry in chosen.impl.kinds else None


def _client_error(chosen: Any, e: Exception) -> LLMError:
    return e if isinstance(e, LLMError) else LLMError(f"The model client '{chosen.label}' failed: {type(e).__name__}: {str(e)[:200]}",
                                                       kind="auth" if isinstance(e, PermissionError) else "server")


def _hold(chosen: Any) -> dict[str, Any]:
    """The auth breaker's key for a client; raises while the client is being held for refusing its credentials."""
    key = auth_breaker.client_key(chosen.id, chosen.label)
    if (held := auth_breaker.check(key)) is not None:
        raise LLMError(held, kind="auth")
    return key


def _fall_back(chosen: Any, e: Exception) -> str:
    """Why the call goes to the default instead, or raises the error when it must not: only a default no less private than the
    selected client may be sent the prompt."""
    err = _client_error(chosen, e)
    if err.kind == "cancelled" or not at_least_as_private(default_client.locality, chosen.impl.locality):
        raise err from e
    why = f"The model client '{chosen.label}' failed before replying ({str(err)[:120]}); the default client answered."
    runtime.active.announce("model.client", chosen.id, default_client.id, why, False)
    return why


def _stopped(reason: str) -> dict[str, Any]:
    return {"type": "end", "finish_reason": reason, "tool_calls": [], "usage": None}


async def _drop(task: asyncio.Future[Any]) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _left(idle: float, deadline_at: float | None) -> float:
    return idle if deadline_at is None else min(idle, deadline_at - time.monotonic())


async def _first(work: asyncio.Future[Any], stopper: asyncio.Future[Any] | None, timeout: float) -> str:
    """Wait for `work`, Stop or the timeout, whichever comes first: "done", "stopped" or "timeout". If the caller is cancelled
    meanwhile, `work` is dropped with it."""
    try:
        await asyncio.wait({work, *([stopper] if stopper else [])}, timeout=max(timeout, 0.01), return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        await _drop(work)
        raise
    if stopper is not None and stopper.done():
        return "stopped"
    return "done" if work.done() else "timeout"


async def _bounded(events: AsyncIterator[Any], idle: float, cancel: asyncio.Event | None, deadline_at: float | None) -> AsyncIterator[Any]:
    """A client's events, ended early (finish_reason cancelled or timeout, no tool calls) by Stop or the deadline. A client silent
    for `idle` seconds raises LLMError, as a provider that stops answering does."""
    stopper = asyncio.ensure_future(cancel.wait()) if cancel is not None else None
    try:
        while True:
            nxt = asyncio.ensure_future(events.__anext__())
            outcome = await _first(nxt, stopper, _left(idle, deadline_at))
            if outcome != "done":
                await _drop(nxt)
            if outcome == "stopped":
                yield _stopped("cancelled")
                return
            if outcome == "timeout":
                if deadline_at is not None and time.monotonic() >= deadline_at:
                    yield _stopped("timeout")
                    return
                raise LLMError(f"The provider stopped responding: no data for {int(idle)}s. Try again.", kind="timeout")
            try:
                ev = nxt.result()
            except StopAsyncIteration:
                return
            yield ev
    finally:
        if stopper is not None:
            stopper.cancel()


async def _stream_client(chosen: Any, settings: dict[str, Any], req: ModelRequest, cancel: asyncio.Event | None) -> AsyncIterator[dict[str, Any]]:
    """One chat call on a swapped-in client: its events held to the stream shape, with the usage row, the model hooks, the auth
    breaker, the idle limit, Stop and the deadline applied here, so no client can skip them."""
    key = _hold(chosen)
    t0, p_chars = time.time(), _prompt_chars(req.messages, req.tools)
    _emit_request(req.model, req.messages, req.tools)
    events = chosen.impl.stream(req).__aiter__()
    settled = False
    try:
        async for ev in checked.checked(_bounded(events, _idle_s(settings), cancel, stream_deadline.get()), p_chars):
            if ev["type"] == "end":
                auth_breaker.success(key)
                settled = True
                _emit_usage(req.model, req.kind, ev["usage"], int((time.time() - t0) * 1000), p_chars, ev["usage_est"]["completion_tokens"] * 4)
            yield ev
    except PermissionError:
        auth_breaker.failure(key, 401, "")
        settled = True
        raise
    finally:
        if not settled:
            auth_breaker.inconclusive(key)
        if close := getattr(events, "aclose", None):
            await close()


async def stream_chat(
    settings: dict[str, Any], model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, kind: str = "chat",
    effort: str = "default", tool_choice: str = "auto", fast: bool = False, cancel: asyncio.Event | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream a chat completion from the model client the chat selects: the built-in HTTP client (`_stream_openai`, which
    documents the events) unless an extension's client is selected. That client gets the request without `settings`. If it
    fails before its first event the default answers instead, but only when the default is no less private; after an event
    nothing is replayed and the error ends the turn."""
    chosen = _alternative_client("chat")
    if chosen is None:
        async for ev in _stream_openai(settings, model, messages, tools, kind=kind, effort=effort, tool_choice=tool_choice, fast=fast, cancel=cancel):
            yield ev
        return
    started = False
    try:
        async for ev in _stream_client(chosen, settings, ModelRequest(model, messages, tools, kind, effort, tool_choice, fast), cancel):
            started = True
            yield ev
        return
    except Exception as e:  # noqa: BLE001 - a client may fail any way it likes; the core decides what the turn sees
        if started:
            raise _client_error(chosen, e) from e
        notice = _fall_back(chosen, e)
    async for ev in _stream_openai(settings, model, messages, tools, kind=kind, effort=effort, tool_choice=tool_choice, fast=fast, cancel=cancel):
        yield {**ev, "notice": notice} if ev["type"] == "end" else ev


async def _client_complete(chosen: Any, settings: dict[str, Any], req: ModelRequest, cancel: asyncio.Event | None,
                           deadline: float | None) -> str:
    key = _hold(chosen)
    task = asyncio.ensure_future(chosen.impl.complete(req))
    stopper = asyncio.ensure_future(cancel.wait()) if cancel is not None else None
    try:
        outcome = await _first(task, stopper, _left(_idle_s(settings), deadline))
        if outcome == "stopped":
            raise LLMError("Stopped.", kind="cancelled")
        if outcome == "timeout":
            raise LLMError("The model provider took too long.", kind="timeout")
        reply = task.result()
    except PermissionError:
        auth_breaker.failure(key, 401, "")
        raise
    except BaseException:
        auth_breaker.inconclusive(key)
        raise
    finally:
        task.cancel()
        if stopper is not None:
            stopper.cancel()
    if not isinstance(reply, ModelReply) or not isinstance(reply.text, str):
        raise LLMError(f"The model client '{chosen.id}' did not return a ModelReply.", kind="server")
    auth_breaker.success(key)
    return reply.text


async def complete(settings: dict[str, Any], model: str, messages: list[dict[str, Any]], kind: str = "other", *,
                   effort: str = "default", cancel: asyncio.Event | None = None, deadline: float | None = None) -> str:
    """A non-streaming completion from the selected model client (see `_complete_openai` for the built-in one's contract).
    A failing extension client is replaced by the default once, only when the default is no less private. The safety reviewer
    (kind "review") always uses the default: no extension may answer "allow" for a call."""
    chosen = _alternative_client("complete") if kind != "review" else None
    if chosen is None:
        return await _complete_openai(settings, model, messages, kind, effort=effort, cancel=cancel, deadline=deadline)
    t0 = time.time()
    _emit_request(model, messages)
    try:
        text = await _client_complete(chosen, settings, ModelRequest(model, messages, None, kind, effort), cancel, deadline)
    except Exception as e:  # noqa: BLE001
        _fall_back(chosen, e)
        return await _complete_openai(settings, model, messages, kind, effort=effort, cancel=cancel, deadline=deadline)
    _emit_usage(model, kind, None, int((time.time() - t0) * 1000), _prompt_chars(messages), len(text))
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
