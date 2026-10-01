"""Provider-agnostic LLM access through a LiteLLM proxy (OpenAI-compatible API)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from contextvars import ContextVar
from typing import Any, AsyncIterator, Callable

import httpx

# Usage accounting. The app registers a listener; callers that know the chat/project set usage_context.
UsageListener = Callable[[dict[str, Any]], None]
_usage_listeners: list[UsageListener] = []
usage_context: ContextVar[dict[str, Any]] = ContextVar("usage_context", default={})


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
    # Offer MCP tools through mcp_tool_search once more than this many are ready (0 = always send every schema).
    "mcpDeferAbove": 12,
    # Per-reply budgets; 0 = unlimited. A run that hits one still writes a final answer, marked partial.
    "maxRunTokens": 200_000,
    "maxRunSeconds": 300,
    "maxRunCost": 0.50,
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
    cancelled = False
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=None)) as client:
        async with client.stream(
            "POST",
            f"{_base(settings)}/v1/chat/completions",
            headers=_headers(settings),
            json=body,
        ) as r:
            if r.status_code >= 400:
                err_body = (await r.aread()).decode("utf-8", "replace")
                raise LLMError(f"{r.status_code} {r.reason_phrase}: {err_body[:500]}")
            # Closes the socket from a second task so a read blocked on the next token returns at once.
            abort = asyncio.create_task(_close_when(cancel, r)) if cancel is not None else None
            try:
                async for line in r.aiter_lines():
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
            except Exception:
                if cancel is None or not cancel.is_set():
                    raise
                cancelled = True
            finally:
                if abort is not None:
                    abort.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await abort
    if cancelled or (cancel is not None and cancel.is_set()):
        # A half-parsed tool call must not run. The caller keeps whatever text already streamed.
        calls = {}
        finish = "cancelled"
    # Reasoning is billed as completion tokens, so it counts toward cost and the run budget.
    p_chars, c_chars = len(json.dumps(messages)), out_chars + reason_chars + sum(len(c["arguments"]) for c in calls.values())
    _emit_usage(model, kind, usage, int((time.time() - t0) * 1000), p_chars, c_chars)
    # usage_est is always present: this route often omits `usage` on streamed replies, and a budget cannot run on None.
    yield {"type": "end", "finish_reason": finish, "tool_calls": [calls[i] for i in sorted(calls)], "usage": usage,
           "usage_est": {"prompt_tokens": p_chars // 4, "completion_tokens": c_chars // 4}}


async def complete(settings: dict[str, Any], model: str, messages: list[dict[str, str]], kind: str = "learn") -> str:
    """Non-streaming completion (used for extraction)."""
    t0 = time.time()
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{_base(settings)}/v1/chat/completions",
            headers=_headers(settings),
            json={"model": model, "messages": messages, "stream": False},
        )
    if r.status_code >= 400:
        raise LLMError(f"{r.status_code}: {r.text[:500]}")
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
