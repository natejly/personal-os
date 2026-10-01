"""Provider-agnostic LLM access through a LiteLLM proxy (OpenAI-compatible API)."""
from __future__ import annotations

import asyncio
import contextlib
import email.utils
import json
import logging
import random
import time
from contextvars import ContextVar
from typing import Any, AsyncIterator, Callable

import httpx

log = logging.getLogger("personal_os.llm")

# Usage accounting. The app registers a listener; callers that know the chat/project set usage_context.
UsageListener = Callable[[dict[str, Any]], None]
_usage_listeners: list[UsageListener] = []
usage_context: ContextVar[dict[str, Any]] = ContextVar("usage_context", default={})
# Absolute time.monotonic() by which the current reply's stream must be over (the run's wall-clock budget). A
# context var rather than a parameter so every caller of stream_chat keeps its signature; None = no limit.
stream_deadline: ContextVar[float | None] = ContextVar("stream_deadline", default=None)


def on_usage(fn: UsageListener) -> None:
    _usage_listeners.append(fn)


def _emit_usage(model: str, kind: str, usage: dict[str, Any] | None, duration_ms: int, prompt_chars: int, completion_chars: int) -> None:
    est = not usage or usage.get("prompt_tokens") is None
    rec = {
        "model": model, "kind": kind, "duration_ms": duration_ms, "estimated": est,
        "prompt_tokens": int((usage or {}).get("prompt_tokens") or prompt_chars // 4),
        "completion_tokens": int((usage or {}).get("completion_tokens") or completion_chars // 4),
        **usage_context.get(),
    }
    for fn in _usage_listeners:
        try:
            fn(rec)
        except Exception:  # noqa: BLE001 - accounting must never break a reply
            pass

DEFAULT_SETTINGS: dict[str, Any] = {
    "baseUrl": "http://localhost:4000",
    "apiKey": "",
    "defaultModel": "gpt-4o",
    "systemPrompt": (
        "You are the assistant inside the user's personal AI OS. Be direct, concise, and useful. "
        "Use markdown when it helps. You may be given memories, a knowledge graph, and document "
        "excerpts as context; use them when relevant and don't mention them unless asked."
    ),
    "extractionModel": "",
    "autoLearn": True,
    # Bank long messages the user writes as style samples and keep their voice profile current (style.py).
    # Independent of autoLearn: wanting the app to learn facts is not the same as wanting it to copy your voice.
    "learnStyle": True,
    "theme": "dark",
    "accent": "sage",
    "mode": "classic",
    "gatherShortcut": "Control+Alt+Command+Space",
    # Shell modularity: Today-screen cards ({key: bool}, missing = shown) and sidebar views the user removed.
    "homeWidgets": {},
    "hiddenViews": [],
    # tools: {tool_name: bool}; missing = on
    "tools": {},
    # How doc_edit lands. "review" proposes a diff; "apply" writes it. Missing means review.
    "docEditMode": "review",
    "maxToolRounds": 25,
    # Per-reply budgets; 0 = unlimited. A run that hits one still writes a final answer, marked partial.
    "maxRunTokens": 200_000,
    "maxRunSeconds": 300,
    "maxRunCost": 0.50,
    # Provider resilience (retry/backoff section below). Retries only happen before a reply's first token;
    # llmIdleSeconds is how long a stream may go without a byte before it is abandoned with a clear error.
    "llmRetries": 3,
    "llmIdleSeconds": 90,
    # Retention (retention.py): days of history kept in tables that only ever grow. User content is never pruned.
    "retainUsageDays": 365,
    "retainTraceDays": 60,
    "retainToolResultDays": 30,
    "retainApprovalDays": 90,
    # Cowork desks. A desk runs bounded turns unattended, so both axes are caps on the whole
    # desk rather than on one reply; 0 on either means unlimited. deskMaxLive bounds how many
    # desks may be running at once, which is the cap the user actually feels.
    # How long a desk waits on a card nobody is watching before letting the run go. The card stays
    # pending and decidable; only the run lets go. 0 = wait forever, which is what a chat does.
    "parkAfterSeconds": 180,
    "deskMaxTurns": 12,
    "deskMaxCost": 2.0,
    "deskMaxLive": 4,
    # Hosts fetch_url may still read once a reply has touched untrusted content (registrable-suffix match).
    "fetchAllowlist": [],
    # Undo window on outgoing mail (outbox.py). `seconds` is clamped to 60-120 on read.
    "gmailSendHold": {"enabled": True, "seconds": 90},
    "braveApiKey": "",
    "tavilyApiKey": "",
    # Without a Brave/Tavily key, web_search uses Exa (keyless via its hosted MCP server; a key lifts the rate limit).
    "exaApiKey": "",
    # fetch_url retries a blocked or JavaScript-only page through Jina Reader (r.jina.ai), which then sees the URL.
    "readerFallback": True,
    # github_search/github_read; empty = the gh CLI's login (`gh auth token`), else unauthenticated (60 requests/h).
    "githubToken": "",
    # {model: {"input": $/M tokens, "output": $/M tokens}} overrides for cost accounting (proxy prices are used otherwise)
    "modelPrices": {},
    "googleClientId": "",
    "googleClientSecret": "",
    "googleToken": {},
    # Activity monitor. Shape and defaults live in activity.DEFAULT_CONFIG; patched through
    # /activity/config rather than /settings so the merge is a deep one.
    "activity": {"enabled": False},
    # Meetings. Shape and defaults live in meetings.DEFAULT_CONFIG; patched through
    # /meetings/config rather than /settings so the merge is a deep one.
    "meetings": {"enabled": False},
    # Google Tasks <-> todos sync. Shape and defaults live in gtasks.DEFAULT_CONFIG; patched
    # through /integrations/google/tasks-sync rather than /settings for the same reason.
    # Empty on purpose: anything named here would override that module's defaults.
    "googleTasksSync": {},
    # todos -> Google Calendar mirror; defaults in todocal.DEFAULT_CONFIG, patched through
    # /integrations/google/todo-calendar.
    "googleTodoCalendar": {},
}


class LLMError(Exception):
    pass


# ---------------- retry / timeouts ----------------
# A transient failure (rate limit, provider 5xx, dropped connection) is retried with exponential backoff and
# jitter, but ONLY while nothing has been streamed to the caller: after the first token a retry would repeat
# or contradict text the user already read, so a mid-reply failure is reported instead.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 529})
RETRY_BASE_S = 1.0
RETRY_CAP_S = 30.0
# A Retry-After longer than this is not worth holding a reply open for; say so instead.
RETRY_AFTER_MAX_S = 60.0
CONNECT_TIMEOUT_S = 10.0
DEFAULT_IDLE_S = 90.0


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


def describe_http_error(status: int, reason: str, body: str, retried: int = 0, retry_after: float | None = None) -> str:
    """A sentence a person can act on, for a failed provider response."""
    tail = f" Retried {retried} time{'s' if retried != 1 else ''}." if retried else ""
    detail = _provider_message(body)
    if status == 429:
        wait = f" The provider asked to wait {int(retry_after)}s." if retry_after else ""
        return f"The provider is rate-limiting you (429).{tail}{wait} Wait a moment and try again, or pick another model."
    if status in (401, 403):
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


async def _send_with_retry(client: httpx.AsyncClient, settings: dict[str, Any], body: dict[str, Any], *, stream: bool,
                           cancel: asyncio.Event | None = None) -> tuple[httpx.Response, Any]:
    """POST the completion request, retrying 429/5xx/connection failures. Returns (response, stream context or None).

    A streamed response comes back open: the caller closes it through the context. A non-2xx answer that survives
    the retries (or is not retryable) raises LLMError with a readable message.
    """
    retries = _retries(settings)
    attempt = 0
    url = f"{_base(settings)}/v1/chat/completions"
    while True:
        cm = client.stream("POST", url, headers=_headers(settings), json=body) if stream else None
        try:
            if cm is not None:
                r = await cm.__aenter__()
                if r.status_code >= 400:
                    await r.aread()
            else:
                r = await client.post(url, headers=_headers(settings), json=body)
        except (httpx.TransportError, httpx.ProtocolError) as e:
            if cm is not None:
                with contextlib.suppress(Exception):
                    await cm.__aexit__(None, None, None)
            if cancel is not None and cancel.is_set():
                raise LLMError("Stopped.") from e
            if attempt >= retries:
                raise LLMError(describe_transport_error(e, attempt)) from e
            attempt += 1
            delay = retry_delay(attempt)
            log.warning("provider connection failed (%s); retry %d/%d in %.1fs", type(e).__name__, attempt, retries, delay)
            if not await _backoff(delay, cancel):
                raise LLMError("Stopped while waiting to retry the provider.") from e
            continue
        if r.status_code < 400:
            return r, cm
        retry_after = parse_retry_after(r.headers.get("retry-after"))
        if cm is not None:
            with contextlib.suppress(Exception):
                await cm.__aexit__(None, None, None)
        too_long = retry_after is not None and retry_after > RETRY_AFTER_MAX_S
        if r.status_code in RETRYABLE_STATUS and attempt < retries and not too_long:
            attempt += 1
            delay = retry_delay(attempt, retry_after)
            log.warning("provider answered %d; retry %d/%d in %.1fs", r.status_code, attempt, retries, delay)
            if not await _backoff(delay, cancel):
                raise LLMError("Stopped while waiting to retry the provider.")
            continue
        raise LLMError(describe_http_error(r.status_code, r.reason_phrase, r.text, attempt, retry_after))


def _base(settings: dict[str, Any]) -> str:
    return str(settings.get("baseUrl") or DEFAULT_SETTINGS["baseUrl"]).rstrip("/")


def _headers(settings: dict[str, Any]) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if settings.get("apiKey"):
        h["Authorization"] = f"Bearer {settings['apiKey']}"
    return h


async def list_models(settings: dict[str, Any]) -> list[dict[str, str]]:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{_base(settings)}/v1/models", headers=_headers(settings))
    if r.status_code >= 400:
        raise LLMError(f"{r.status_code}: {r.text[:300]}")
    data = r.json().get("data", [])
    return sorted(({"id": m["id"]} for m in data if "id" in m), key=lambda m: m["id"])


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
     "usage_est": {"prompt_tokens": int, "completion_tokens": int}}.

    `cancel`, once set, closes the HTTP stream immediately. The final event then has finish_reason
    "cancelled" and no tool calls, including any arguments that had only partly arrived.

    The `stream_deadline` context var (a time.monotonic() value) bounds the whole stream: when it passes, the stream ends the same
    way with finish_reason "timeout". A stream that goes `llmIdleSeconds` without a byte raises LLMError.
    Connect failures, 429 and 5xx are retried (with backoff) until the first byte; never after.
    """
    body: dict[str, Any] = {"model": model, "messages": messages, "stream": True, "stream_options": {"include_usage": True}}
    # Only sent when asked for: a model that does not support it rejects the whole request.
    if effort and effort != "default":
        body["reasoning_effort"] = effort
    if fast:
        body["service_tier"] = "priority"
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice
    calls: dict[int, dict[str, Any]] = {}
    finish: str | None = None
    usage: dict[str, Any] | None = None
    t0 = time.time()
    out_chars = 0
    reason_chars = 0
    cancelled = timed_out = False
    idle = _idle_s(settings)
    deadline_at = stream_deadline.get()
    # read= bounds the wait for response headers (and backs up a chunk); the per-chunk idle limit is enforced below.
    async with httpx.AsyncClient(timeout=httpx.Timeout(CONNECT_TIMEOUT_S, read=idle + 5, write=30, pool=CONNECT_TIMEOUT_S)) as client:
        r, cm = await _send_with_retry(client, settings, body, stream=True, cancel=cancel)
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
                        raise LLMError(f"The provider stopped responding: no data for {int(idle)}s mid-reply. Try again.") from None
                    if cancel is not None and cancel.is_set():
                        cancelled = True
                        break
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    if obj.get("error"):
                        err = obj["error"]
                        raise LLMError(err.get("message") if isinstance(err, dict) else str(err))
                    if isinstance(obj.get("usage"), dict):
                        usage = {k: obj["usage"].get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens") if obj["usage"].get(k) is not None}
                    choice = (obj.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    reason = _reason_text(delta)
                    if reason:
                        reason_chars += len(reason)
                        yield {"type": "reasoning", "text": reason}
                    if delta.get("content"):
                        out_chars += len(delta["content"])
                        yield {"type": "delta", "text": delta["content"]}
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        cur = calls.setdefault(idx, {"id": tc.get("id") or f"call_{idx}", "name": "", "arguments": ""})
                        if tc.get("id"):
                            cur["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            cur["name"] += fn["name"]
                        if fn.get("arguments"):
                            cur["arguments"] += fn["arguments"]
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if cancel is None or not cancel.is_set():
                    if isinstance(e, httpx.HTTPError):  # after the first byte: reported, never retried
                        raise LLMError(describe_transport_error(e) + " The reply was cut off.") from e
                    raise
                cancelled = True
            finally:
                if abort is not None:
                    abort.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await abort
        finally:
            with contextlib.suppress(Exception):
                await cm.__aexit__(None, None, None)
    if cancelled or (cancel is not None and cancel.is_set()):
        # A half-parsed tool call must not run. The caller keeps whatever text already streamed.
        calls = {}
        finish = "cancelled"
    elif timed_out:
        calls = {}
        finish = "timeout"
    # Reasoning is billed as completion tokens, so it counts toward cost and the run budget.
    p_chars, c_chars = len(json.dumps(messages)), out_chars + reason_chars + sum(len(c["arguments"]) for c in calls.values())
    _emit_usage(model, kind, usage, int((time.time() - t0) * 1000), p_chars, c_chars)
    # usage_est is always present: this route often omits `usage` on streamed replies, and a budget cannot run on None.
    yield {"type": "end", "finish_reason": finish, "tool_calls": [calls[i] for i in sorted(calls)], "usage": usage,
           "usage_est": {"prompt_tokens": p_chars // 4, "completion_tokens": c_chars // 4}}


async def complete(settings: dict[str, Any], model: str, messages: list[dict[str, str]], kind: str = "learn") -> str:
    """Non-streaming completion (used for extraction)."""
    t0 = time.time()
    async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=CONNECT_TIMEOUT_S)) as client:
        r, _ = await _send_with_retry(client, settings, {"model": model, "messages": messages, "stream": False}, stream=False)
    data = r.json()
    text = data["choices"][0]["message"]["content"] or ""
    _emit_usage(model, kind, data.get("usage") if isinstance(data.get("usage"), dict) else None, int((time.time() - t0) * 1000), len(json.dumps(messages)), len(text))
    return text


def audio_usage(model: str, seconds: float, kind: str = "meeting-stt") -> None:
    """Record transcribed audio in the usage log; duration_ms carries the audio length, not wall time.

    Synchronous and silent on purpose: transcription runs on worker threads, and a missing
    accounting row must never fail a transcription.
    """
    try:
        _emit_usage(model, kind, None, int(seconds * 1000), 0, 0)
    except Exception:  # noqa: BLE001 - accounting must never break a transcription
        pass
