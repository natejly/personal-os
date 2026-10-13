"""Local, append-only replay traces: one JSONL line per chat request, plus follow-up and boundary events.

`<data_dir>/traces/YYYY-MM-DD.jsonl` (UTC), one JSON object per line, `"schema": 1`, `"type"` in
trace | followup | boundary. Names follow the OTel GenAI conventions where one exists (as otel_export.py), `grain.*` otherwise.

Privacy: local only, no network. Every free-text field and tool argument is scrubbed with redact.scrub_secrets and credential-named
keys are dropped. The system prompt, skills and memories are recorded as hashes only.
Safety: building and writing happen on one worker thread, and every entry point swallows its errors, so a logging fault never reaches a chat.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .otel_export import SERVICE_VERSION
from .redact import scrub_secrets

log = logging.getLogger(__name__)

SCHEMA = 1
SESSION_ID = uuid.uuid4().hex  # one per backend process
MAX_TEXT = 20_000
_CRED_KEY = re.compile(r"api[_-]?key|token(?!s)|secret|passw|auth(?!ors?\b)|cookie|credential|bearer|private[_-]?key|session[_-]?id", re.I)
_CORRECTION = re.compile(
    r"^\W*(no(?!\s+(problem|worries|thanks|thank|need)\b)|nope|nah|wrong|incorrect|actually|instead|"
    r"that(?:'|’)?s (?:wrong|not|incorrect)|that is (?:wrong|not|incorrect)|not what i|i meant|you forgot)\b", re.I)

_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="replay-trace")


def is_correction(text: str) -> bool:
    """A new user message that starts like a correction of the reply before it. Deterministic, leading phrases only."""
    return bool(_CORRECTION.match(text or ""))


def tier_of(model: str, tiers: dict[str, str], default: str, routed: bool) -> str:
    """Which tier the requested model is: auto-routed, a configured tier model, the default, else custom."""
    if routed:
        return "auto"
    return next((t for t, m in tiers.items() if m and m == model), "default" if model == default else "custom")


def clean(v: Any, _depth: int = 0) -> Any:
    """Redacted copy: strings scrubbed and capped, credential-named keys dropped, non-JSON values stringified."""
    if isinstance(v, str):
        return scrub_secrets(v)[:MAX_TEXT]
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    if _depth > 6:
        return None
    if isinstance(v, dict):
        return {str(k): clean(x, _depth + 1) for k, x in v.items() if not _CRED_KEY.search(str(k))}
    if isinstance(v, (list, tuple, set)):
        return [clean(x, _depth + 1) for x in list(v)[:200]]
    return scrub_secrets(str(v))[:MAX_TEXT]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write(data_dir: Path | str, rec: dict[str, Any]) -> None:
    day = datetime.fromtimestamp(rec["ts"] / 1000, timezone.utc).strftime("%Y-%m-%d")
    d = Path(data_dir) / "traces"
    line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
    with _lock:  # one append at a time, so concurrent requests never interleave a line
        d.mkdir(parents=True, exist_ok=True)
        with open(d / f"{day}.jsonl", "a", encoding="utf-8") as f:
            f.write(line)


def _submit(data_dir: Path | str | None, build: Any) -> None:
    if data_dir is None:
        return
    def job() -> None:
        try:
            _write(data_dir, build())
        except Exception:  # noqa: BLE001 - logging must never break a chat
            log.warning("replay trace write failed", exc_info=True)
    try:
        _pool.submit(job)
    except Exception:  # noqa: BLE001
        log.warning("replay trace submit failed", exc_info=True)


def flush() -> None:
    """Wait for queued writes (tests, shutdown)."""
    _pool.submit(lambda: None).result(timeout=10)


def _outcome(ev: dict[str, Any]) -> str:
    if ev.get("pending"):
        return "parked"
    if ev.get("blocked") or ev.get("blocked_by") or ev.get("approval") in ("deny", "denied"):
        return "denied"
    return "error" if ev.get("error") else "ok"


def trace(data_dir: Path | str, *, conv_id: str, message_id: str, user_msg_id: str | None, user_text: str, reply: str,
          requested_model: str, cfg: dict[str, Any], routed: bool, model: str, used: dict[str, Any] | None, spans: list[dict[str, Any]],
          tool_events: list[dict[str, Any]], cost: float | None, outcome: str, error: str | None = None) -> None:
    """Queue one request's trace. `outcome` is ok | error | stopped. The inputs are snapshotted by the worker, so pass live lists freely."""
    try:
        from . import providers
        tier = tier_of(requested_model, {t: providers.tier_model(cfg, t) for t in providers.TIERS}, str(cfg.get("defaultModel") or ""), routed)
        spans, tool_events, used = list(spans), list(tool_events), dict(used or {})
        used["volatile_blocks"] = list(used.get("volatile_blocks") or [])
    except Exception:  # noqa: BLE001
        log.warning("replay trace snapshot failed", exc_info=True)
        return

    def build() -> dict[str, Any]:
        llm = [s for s in spans if s.get("kind") == "llm"]
        u = lambda k: sum(int(((s.get("meta") or {}).get("usage") or {}).get(k) or 0) for s in llm)  # noqa: E731
        t0 = min((s["start"] for s in spans), default=int(time.time() * 1000))
        t1 = max((s.get("end") or s["start"] for s in spans), default=t0)
        from .context import estimate_tokens  # lazy: context imports half the app
        named = [("stable_prefix", str(used["stable_system"]))] if used.get("stable_system") else []
        for b in used["volatile_blocks"]:
            first = next((ln for ln in str(b).splitlines() if ln.strip()), "")
            named.append((clean(re.sub(r"[#*_`:]+", "", first).strip()[:60]) or "volatile", str(b)))
        ctx = [*({"name": k, "tokens": n} for k, n in (used.get("section_tokens") or {}).items()),
               *({"name": k, "tokens": estimate_tokens(v)} for k, v in named if v), {"name": "total", "tokens": used.get("tokens_estimate")}]
        return {
            "schema": SCHEMA, "type": "trace", "ts": t1 or int(time.time() * 1000), "trace_id": uuid.uuid4().hex,
            "gen_ai.response.id": message_id, "session.id": SESSION_ID, "gen_ai.conversation.id": conv_id,
            "service.version": SERVICE_VERSION, "grain.message.user_id": user_msg_id,
            "grain.prompt.hash": _hash(str(used.get("stable_system") or used.get("system_prompt") or "")),
            "grain.skills.hash": _hash(json.dumps(sorted((str(x.get("id")), str(x.get("name")), str(x.get("description")), str(x.get("disclosure"))) for x in used.get("skills") or []))),
            "grain.memory.hash": _hash(json.dumps(sorted((str(x.get("id")), str(x.get("content"))) for x in used.get("memories") or []))),
            "grain.model.tier_requested": tier, "gen_ai.request.model": requested_model,
            "gen_ai.response.model": next((s["meta"]["model"] for s in reversed(llm) if (s.get("meta") or {}).get("model")), model),
            "grain.context.sections": [c for c in ctx if c["tokens"] is not None],
            "gen_ai.input.messages": [{"role": "user", "parts": [{"type": "text", "content": clean(user_text)}]}],
            "gen_ai.output.messages": [{"role": "assistant", "parts": [{"type": "text", "content": clean(reply)}]}],
            "tool_calls": [{"gen_ai.tool.name": e.get("name"), "gen_ai.tool.call.id": e.get("id"),
                            "gen_ai.tool.call.arguments": clean(e.get("arguments") or {}), "grain.tool.outcome": _outcome(e),
                            "error.type": "tool_error" if e.get("error") else None, "grain.tool.error": clean(e.get("error")),
                            "grain.latency_ms": e.get("duration_ms")} for e in tool_events],
            "spans": [{"id": s.get("id"), "name": s.get("name"), "kind": s.get("kind"), "start": s.get("start"), "end": s.get("end"),
                       "latency_ms": (s["end"] - s["start"]) if s.get("end") else None, "parent_id": s.get("parent_id"),
                       "error": clean(s.get("error"))} for s in spans],
            "gen_ai.usage.input_tokens": u("prompt_tokens"), "gen_ai.usage.output_tokens": u("completion_tokens"),
            "gen_ai.usage.cache_read.input_tokens": u("cached_tokens"), "gen_ai.usage.reasoning.output_tokens": u("reasoning_tokens"),
            "grain.cost.usd": cost, "grain.latency_ms": max(0, t1 - t0), "grain.outcome": outcome, "error.message": clean(error),
        }
    _submit(data_dir, build)


def followup(data_dir: Path | str, *, conv_id: str, kind: str, message_id: str | None, text: str | None = None) -> None:
    """kind: edit | retry | regenerate | correction. `message_id` is the assistant message the follow-up concerns."""
    def build() -> dict[str, Any]:
        rec = {"schema": SCHEMA, "type": "followup", "ts": int(time.time() * 1000), "gen_ai.response.id": message_id,
               "gen_ai.conversation.id": conv_id, "session.id": SESSION_ID, "grain.followup.kind": kind}
        if kind == "correction":
            rec["grain.correction.text"] = clean(text or "")
        return rec
    _submit(data_dir, build)


def boundary(data_dir: Path | str, conv_id: str, kind: str, tokens_before: int | None = None, tokens_after: int | None = None) -> None:
    """kind: clear | compact."""
    def build() -> dict[str, Any]:
        rec: dict[str, Any] = {"schema": SCHEMA, "type": "boundary", "ts": int(time.time() * 1000), "gen_ai.conversation.id": conv_id,
                               "session.id": SESSION_ID, "grain.boundary.kind": kind}
        if kind == "compact":
            rec.update({"grain.compact.tokens_before": tokens_before, "grain.compact.tokens_after": tokens_after})
        return rec
    _submit(data_dir, build)
