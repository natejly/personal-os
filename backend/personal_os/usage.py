"""LLM usage accounting: one row per model call (chat rounds and auto-learn), with cost.

Prices, all in USD per 1M tokens, from (later wins): the LiteLLM proxy's /model/info (its bundled price map),
the Fireworks list prices below, and the user's overrides in settings["modelPrices"] as
{"model": {"input": ..., "output": ...}}. A model none of them prices stays unpriced (cost NULL, shown as
unknown, never $0); nothing is guessed.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any

import httpx

from . import providers
from .db import Database, new_id, now

PRICE_TTL = 600

# Fireworks serverless list prices (Standard tier), USD per 1M tokens: input, cached input, output.
# Source: https://docs.fireworks.ai/serverless/pricing and https://fireworks.ai/pricing, retrieved 2026-10-07.
# deepseek-v4p1-flash changed on 2026-10-01 (was 0.22 / 0.007 / 0.66). Keyed by short_model(); add a model only
# from that page. Embeddings are billed on input tokens only.
FIREWORKS_PRICES: dict[str, dict[str, float]] = {
    "ember-1": {"input": 3.00, "cache_read": 0.30, "output": 15.00},
    "glm-5p3": {"input": 1.40, "cache_read": 0.26, "output": 4.40},
    "glm-5p3-flash": {"input": 0.15, "cache_read": 0.03, "output": 0.50},
    "kimi-k3": {"input": 3.00, "cache_read": 0.30, "output": 15.00},
    "kimi-k3-fast": {"input": 4.50, "cache_read": 0.45, "output": 22.50},
    "deepseek-v4p1-flash": {"input": 0.30, "cache_read": 0.006, "output": 1.20},
    "qwen3-embedding-8b": {"input": 0.10},
}


# Proxy model names (litellm.yaml) that bill as a differently named Fireworks model. A '.' in a name is Fireworks'
# 'p' ('glm-5.3' -> 'glm-5p3'), which price_key() handles without a row here.
FIREWORKS_ALIASES: dict[str, str] = {"deepseek-v4-flash": "deepseek-v4p1-flash"}


def short_model(model: str) -> str:
    """'accounts/fireworks/models/ember-1' -> 'ember-1'; 'fireworks_ai/x' -> 'x'. A plain alias is unchanged.

    The proxy and a provider called directly name the same model differently, so prices and the per-model
    breakdown match on this short name when the exact id has no row.
    """
    return (model or "").rstrip("/").rsplit("/", 1)[-1]


def price_key(model: str) -> str:
    """The name a model is priced under: its short name, with a proxy alias resolved to the Fireworks model it routes
    to ('glm-5.3' -> 'glm-5p3', 'deepseek-v4-flash' -> 'deepseek-v4p1-flash') even while the proxy is down."""
    s = short_model(model)
    s = FIREWORKS_ALIASES.get(s, s)
    return s.replace(".", "p") if s not in FIREWORKS_PRICES and s.replace(".", "p") in FIREWORKS_PRICES else s


class Pricing:
    """Per-token prices, cached from the proxy and merged with user overrides."""

    def __init__(self) -> None:
        self._proxy: dict[str, dict[str, float]] = {}
        self._caps: dict[str, dict[str, Any]] = {}
        self._targets: dict[str, str] = {}  # proxy alias -> the model it routes to ('glm-5.3' -> '.../glm-5p3')
        self._fetched = 0.0
        self._base = ""

    def caps(self, model: str) -> dict[str, Any]:
        """{mode?, reasoning?, max_input_tokens?, max_output_tokens?} the proxy (or the Anthropic Models API) reports for a model; {} when unknown."""
        return dict(self._caps.get(model) or {})

    async def refresh(self, settings: dict[str, Any], force: bool = False) -> None:
        base = str(settings.get("baseUrl") or "").rstrip("/")
        if not base:
            return
        if not force and base == self._base and time.time() - self._fetched < PRICE_TTL:
            return
        if base != self._base:
            self._caps = {}
        self._base, self._fetched = base, time.time()
        headers = {"Authorization": f"Bearer {settings['apiKey']}"} if settings.get("apiKey") else {}
        try:
            if providers.infer(base) == "anthropic":
                # The Models API reports each model's output cap (max_tokens), which that provider requires on every call.
                headers = {**headers, "x-api-key": str(settings.get("apiKey") or ""), "anthropic-version": "2023-06-01"}
                async with httpx.AsyncClient(timeout=8) as c:
                    r = await c.get(f"{base}/models", headers=headers, params={"limit": 1000})
                if r.status_code < 400:
                    self._caps = {m["id"]: {k: v for k, v in (("max_input_tokens", m.get("max_input_tokens")), ("max_output_tokens", m.get("max_tokens"))) if v}
                                  for m in r.json().get("data", []) if m.get("id")}
                return
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(f"{base}/model/info", headers=headers)
            if r.status_code >= 400:
                return
            out: dict[str, dict[str, float]] = {}
            caps: dict[str, dict[str, Any]] = {}
            targets: dict[str, str] = {}
            for m in r.json().get("data", []):
                info = m.get("model_info") or {}
                target = str((m.get("litellm_params") or {}).get("model") or "")
                if m.get("model_name") and target:
                    targets[m["model_name"]] = target
                if m.get("model_name"):
                    # Independent of prices: a model with no price row still reports what it can do.
                    found = {k: v for k, v in (("mode", info.get("mode")), ("reasoning", info.get("supports_reasoning")),
                                               ("max_input_tokens", info.get("max_input_tokens")),
                                               ("max_output_tokens", info.get("max_output_tokens"))) if v is not None}
                    if found:
                        caps[m["model_name"]] = found
                i, o = info.get("input_cost_per_token"), info.get("output_cost_per_token")
                # A price map row of zeros is a model it has no price for, not a free one.
                if m.get("model_name") and (i or o):
                    row = {k: float(v) * 1e6 for k, v in (("input", i), ("output", o)) if v is not None}
                    if info.get("cache_read_input_token_cost") is not None:
                        row["cache_read"] = float(info["cache_read_input_token_cost"]) * 1e6
                    if info.get("cache_creation_input_token_cost") is not None:
                        row["cache_write"] = float(info["cache_creation_input_token_cost"]) * 1e6
                    out[m["model_name"]] = row
            self._proxy = out
            self._caps = caps
            self._targets = targets
        except Exception:  # noqa: BLE001 - pricing is best effort
            pass

    def table(self, settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """{model: {input?, output?, cache_read?, cache_write?, source: 'proxy'|'fireworks'|'override'}} in $ per million tokens.

        Fireworks list prices replace the proxy's for the same model, under its alias too. An override keeps the
        cached-input price of the row it replaces unless it sets its own."""
        out: dict[str, dict[str, Any]] = {m: {**p, "source": "proxy"} for m, p in self._proxy.items()}
        for alias, target in self._targets.items():
            if short_model(target) in FIREWORKS_PRICES:
                out[alias] = {**FIREWORKS_PRICES[short_model(target)], "source": "fireworks"}
        out.update({m: {**p, "source": "fireworks"} for m, p in FIREWORKS_PRICES.items()})
        for m, p in (settings.get("modelPrices") or {}).items():
            if isinstance(p, dict) and (p.get("input") is not None or p.get("output") is not None):
                base = {k: v for k, v in (out.get(m) or {}).items() if k in ("cache_read", "cache_write")}
                out[m] = {**base, **{k: float(p[k]) for k in ("input", "output", "cache_read", "cache_write") if p.get(k) is not None},
                          "source": "override"}
        return out

    def cost(self, settings: dict[str, Any], model: str, prompt_tokens: int, completion_tokens: int,
             cached_tokens: int = 0, cache_write_tokens: int = 0) -> float | None:
        """Reasoning tokens are already inside completion_tokens, so they cost nothing extra here."""
        # Any row naming this model, by full id or short name; an override beats a list price beats the proxy's,
        # and an exact id beats a short-name match within the same source.
        short, rank = price_key(model), {"override": 0, "fireworks": 1, "proxy": 2}
        rows = [(rank[v["source"]], k != model, v) for k, v in self.table(settings).items()
                if model and (k == model or price_key(k) == short)]
        p = min(rows, key=lambda r: r[:2])[2] if rows else None
        if not p or p.get("input") is None or (completion_tokens and p.get("output") is None):
            return None  # unknown, never $0
        # Cached and cache-write tokens are part of prompt_tokens: each prompt token is billed once, at its own rate.
        cached = max(0, min(int(cached_tokens or 0), prompt_tokens))
        written = max(0, min(int(cache_write_tokens or 0), prompt_tokens - cached))
        uncached = prompt_tokens - cached - written
        return (uncached * p["input"] + cached * p.get("cache_read", p["input"]) + written * p.get("cache_write", p["input"])
                + completion_tokens * p.get("output", 0.0)) / 1e6


# Where a usage_log row came from (its source column). Order is the display order.
SOURCES: dict[str, str] = {"grain": "Grain", "opencode": "OpenCode"}


# What a call was for, from its kind and tag. Order is the display order.
FEATURES: dict[str, str] = {
    "chat": "Chat",
    "jobs": "Scheduled jobs",
    "desks": "Autonomous desks",
    "memory": "Memory and graph",
    "embeddings": "Embeddings",
    "helpers": "Titles, suggestions and reviews",
    "vision": "Vision and images",
    "voice": "Voice",
    "opencode": "OpenCode",
    "other": "Other",
}
_MEMORY_KINDS = {"learn", "style", "recall_index", "graph"}
_EMBED_KINDS = {"embedding", "embeddings", "recall_query"}
_HELPER_KINDS = {"title", "followups", "assist", "review"}
_VISION_KINDS = {"vision", "image"}
_VOICE_KINDS = {"stt", "tts"}


def feature(kind: str, tag: str, model: str = "") -> str:
    """One of FEATURES' keys. Jobs and desks are chat calls told apart by their tag; an embedding model's call is
    an embedding whatever feature asked for it (older rows logged recall embeddings under recall_index)."""
    tag, kind = tag or "", kind or ""
    if tag.startswith("job:"):
        return "jobs"
    if tag.startswith("desk:"):
        return "desks"
    if kind in _EMBED_KINDS or "embed" in short_model(model).lower():
        return "embeddings"
    if tag == "graph-backfill" or kind in _MEMORY_KINDS:
        return "memory"
    if kind == "chat":
        return "chat"
    if kind in _HELPER_KINDS:
        return "helpers"
    if kind in _VISION_KINDS:
        return "vision"
    if kind in _VOICE_KINDS:
        return "voice"
    if kind == "opencode":
        return "opencode"
    return "other"


def period_starts(at: datetime | None = None) -> dict[str, datetime]:
    """Local midnight today, Monday of this week and the 1st of this month."""
    t = (at or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    return {"today": t, "week": t - timedelta(days=t.weekday()), "month": t.replace(day=1)}


def _bucket() -> dict[str, Any]:
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "reasoning_tokens": 0, "cost": 0.0, "unpriced": 0, "ms": 0, "chat_calls": 0, "learn_calls": 0, "other_calls": 0}


def _add(b: dict[str, Any], r: dict[str, Any]) -> None:
    b["calls"] += 1
    b["prompt_tokens"] += r["prompt_tokens"]
    b["completion_tokens"] += r["completion_tokens"]
    b["cached_tokens"] += r["cached_tokens"]
    b["reasoning_tokens"] += r["reasoning_tokens"]
    b["ms"] += r["duration_ms"]
    if r["cost"] is None:
        b["unpriced"] += 1
    else:
        b["cost"] += r["cost"]
    k = r["kind"] if r["kind"] in ("chat", "learn") else "other"
    b[f"{k}_calls"] += 1


def _finish(b: dict[str, Any]) -> dict[str, Any]:
    return {**b, "cost": round(b["cost"], 6), "avg_ms": int(b["ms"] / b["calls"]) if b["calls"] else 0, "tokens": b["prompt_tokens"] + b["completion_tokens"],
            "cache_hit_rate": round(b["cached_tokens"] / b["prompt_tokens"], 4) if b["prompt_tokens"] else 0,
            "reasoning_share": round(b["reasoning_tokens"] / b["completion_tokens"], 4) if b["completion_tokens"] else 0}


class Usage:
    def __init__(self, db: Database):
        self.db = db

    def record(self, *, model: str, kind: str, prompt_tokens: int, completion_tokens: int, duration_ms: int, cost: float | None,
               estimated: bool, conversation_id: str | None, project_id: str | None,
               cached_tokens: int = 0, cache_write_tokens: int = 0, reasoning_tokens: int = 0, tag: str = "", round: int | None = None) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO usage_log(id,created_at,model,kind,conversation_id,project_id,prompt_tokens,completion_tokens,duration_ms,cost,estimated,cached_tokens,cache_write_tokens,reasoning_tokens,tag,round) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_id(), now(), model, kind, conversation_id, project_id, int(prompt_tokens), int(completion_tokens), int(duration_ms), cost, 1 if estimated else 0,
                 int(cached_tokens), int(cache_write_tokens), int(reasoning_tokens), tag or "", int(round or 0)),
            )

    def reprice(self, pricing: Pricing, settings: dict[str, Any]) -> int:
        """Recompute cost for every row a price is known for (after prices change). Returns rows updated.

        A row whose model has no price now keeps its old cost, so switching away from the proxy never erases history."""
        with self.db.tx() as c:
            # A cost OpenCode reported is what it billed; only our own pricing is recomputed.
            rows = c.execute("SELECT id, model, prompt_tokens, completion_tokens, cached_tokens, cache_write_tokens FROM usage_log "
                             "WHERE cost_source IS NOT 'opencode'").fetchall()
            n = 0
            for r in rows:
                cost = pricing.cost(settings, r["model"], r["prompt_tokens"], r["completion_tokens"], r["cached_tokens"], r["cache_write_tokens"])
                if cost is None:
                    continue  # no price now (e.g. the proxy is gone): keep what the call was priced at when it ran
                c.execute("UPDATE usage_log SET cost=? WHERE id=?", (cost, r["id"]))
                n += 1
        return n

    def conversation(self, conv_id: str) -> dict[str, Any]:
        """Everything one chat spent, from the rows tagged with its id. A chat with no rows is a zeroed bucket."""
        with self.db.tx() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM usage_log WHERE conversation_id=? ORDER BY created_at", (conv_id,)).fetchall()]
        total = _bucket()
        by_kind: dict[str, dict[str, Any]] = {}
        for r in rows:
            _add(total, r)
            _add(by_kind.setdefault(r["kind"], _bucket()), r)
        return {"totals": _finish(total), "by_kind": [{"kind": k, **_finish(b)} for k, b in by_kind.items()],
                "since": rows[0]["created_at"] if rows else None, "estimated": sum(1 for r in rows if r["estimated"])}

    def periods(self, at: datetime | None = None) -> dict[str, dict[str, Any]]:
        """Totals for today, this week (from Monday) and this month, whatever range the report shows."""
        starts = period_starts(at)
        first = min(starts.values()).timestamp()
        with self.db.tx() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM usage_log WHERE created_at >= ?", (first,)).fetchall()]
        out = {k: _bucket() for k in starts}
        for r in rows:
            for k, start in starts.items():
                if r["created_at"] >= start.timestamp():
                    _add(out[k], r)
        return {k: {"since": starts[k].strftime("%Y-%m-%d"), **_finish(b)} for k, b in out.items()}

    def report(self, days: int = 30, at: datetime | None = None) -> dict[str, Any]:
        days = max(1, min(int(days), 365))
        start_day = ((at or datetime.now()) - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        since = start_day.timestamp()
        with self.db.tx() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM usage_log WHERE created_at >= ? ORDER BY created_at", (since,)).fetchall()]
            projects = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM projects").fetchall()}

        daily = {(start_day + timedelta(days=i)).strftime("%Y-%m-%d"): _bucket() for i in range(days)}
        hourly = {h: 0 for h in range(24)}
        weekday = {d: 0 for d in range(7)}
        by_model: dict[str, dict[str, Any]] = {}
        by_kind: dict[str, dict[str, Any]] = {}
        by_project: dict[str, dict[str, Any]] = {}
        by_tag: dict[str, dict[str, Any]] = {}
        by_feature: dict[str, dict[str, Any]] = {}
        by_source: dict[str, dict[str, Any]] = {k: _bucket() for k in SOURCES}
        model_ids: dict[str, set[str]] = {}
        total = _bucket()
        for r in rows:
            t = datetime.fromtimestamp(r["created_at"])
            _add(total, r)
            day = t.strftime("%Y-%m-%d")
            if day in daily:
                _add(daily[day], r)
            if r["kind"] == "chat":
                hourly[t.hour] += 1
                weekday[t.weekday()] += 1
            # One row per model however it was addressed: the proxy's 'ember-1' and Fireworks' full id merge.
            name = short_model(r["model"]) or r["model"]
            _add(by_model.setdefault(name, _bucket()), r)
            model_ids.setdefault(name, set()).add(r["model"])
            _add(by_feature.setdefault(feature(r["kind"], r.get("tag") or "", r["model"]), _bucket()), r)
            _add(by_kind.setdefault(r["kind"], _bucket()), r)
            _add(by_source.setdefault(r.get("source") or "grain", _bucket()), r)
            _add(by_tag.setdefault(r.get("tag") or "untagged", _bucket()), r)
            _add(by_project.setdefault(projects.get(r["project_id"], "Personal") if r["project_id"] else "Personal", _bucket()), r)

        return {
            "days": days,
            "totals": _finish(total),
            "daily": [{"day": d, **_finish(b)} for d, b in daily.items()],
            "hourly": [{"hour": h, "calls": n} for h, n in hourly.items()],
            "weekday": [{"weekday": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d], "calls": n} for d, n in weekday.items()],
            "periods": self.periods(at),
            "by_model": sorted(({"model": m, "ids": sorted(model_ids[m]), **_finish(b)} for m, b in by_model.items()), key=lambda x: -x["tokens"]),
            "by_feature": [{"feature": k, "label": FEATURES[k], **_finish(by_feature[k])} for k in FEATURES if k in by_feature],
            "by_kind": [{"kind": k, **_finish(b)} for k, b in by_kind.items()],
            "by_source": [{"source": k, "label": SOURCES.get(k, k), **_finish(b)} for k, b in by_source.items()],
            "by_tag": sorted(({"tag": t, **_finish(b)} for t, b in by_tag.items()), key=lambda x: -x["cost"]),
            "by_project": sorted(({"project": p, **_finish(b)} for p, b in by_project.items()), key=lambda x: -x["tokens"]),
        }


def dumps(v: Any) -> str:
    return json.dumps(v)
