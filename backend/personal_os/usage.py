"""LLM usage accounting: one row per model call (chat rounds and auto-learn), with cost.

Prices come from the LiteLLM proxy's /model/info (its bundled price map) and can be
overridden per model in settings["modelPrices"] as {"model": {"input": $/M tokens, "output": $/M tokens}}.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any

import httpx

from .db import Database, new_id, now

PRICE_TTL = 600


class Pricing:
    """Per-token prices, cached from the proxy and merged with user overrides."""

    def __init__(self) -> None:
        self._proxy: dict[str, dict[str, float]] = {}
        self._fetched = 0.0
        self._base = ""

    async def refresh(self, settings: dict[str, Any], force: bool = False) -> None:
        base = str(settings.get("baseUrl") or "").rstrip("/")
        if not base:
            return
        if not force and base == self._base and time.time() - self._fetched < PRICE_TTL:
            return
        self._base, self._fetched = base, time.time()
        headers = {"Authorization": f"Bearer {settings['apiKey']}"} if settings.get("apiKey") else {}
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(f"{base}/model/info", headers=headers)
            if r.status_code >= 400:
                return
            out: dict[str, dict[str, float]] = {}
            for m in r.json().get("data", []):
                info = m.get("model_info") or {}
                i, o = info.get("input_cost_per_token"), info.get("output_cost_per_token")
                if m.get("model_name") and (i is not None or o is not None):
                    row = {"input": float(i or 0) * 1e6, "output": float(o or 0) * 1e6}
                    if info.get("cache_read_input_token_cost") is not None:
                        row["cache_read"] = float(info["cache_read_input_token_cost"]) * 1e6
                    if info.get("cache_creation_input_token_cost") is not None:
                        row["cache_write"] = float(info["cache_creation_input_token_cost"]) * 1e6
                    out[m["model_name"]] = row
            self._proxy = out
        except Exception:  # noqa: BLE001 - pricing is best effort
            pass

    def table(self, settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """{model: {input, output, source: 'proxy'|'override'}} in $ per million tokens."""
        out: dict[str, dict[str, Any]] = {m: {**p, "source": "proxy"} for m, p in self._proxy.items()}
        for m, p in (settings.get("modelPrices") or {}).items():
            if isinstance(p, dict) and (p.get("input") is not None or p.get("output") is not None):
                row = {"input": float(p.get("input") or 0), "output": float(p.get("output") or 0), "source": "override"}
                for k in ("cache_read", "cache_write"):
                    if p.get(k) is not None:
                        row[k] = float(p[k])
                out[m] = row
        return out

    def cost(self, settings: dict[str, Any], model: str, prompt_tokens: int, completion_tokens: int,
             cached_tokens: int = 0, cache_write_tokens: int = 0) -> float | None:
        """Reasoning tokens are already inside completion_tokens, so they cost nothing extra here."""
        p = self.table(settings).get(model)
        if not p:
            return None
        cached = max(0, min(int(cached_tokens or 0), prompt_tokens))
        written = max(0, min(int(cache_write_tokens or 0), prompt_tokens - cached))
        uncached = prompt_tokens - cached - written
        return (uncached * p["input"] + cached * p.get("cache_read", p["input"]) + written * p.get("cache_write", p["input"])
                + completion_tokens * p["output"]) / 1e6


class Usage:
    def __init__(self, db: Database):
        self.db = db

    def record(self, *, model: str, kind: str, prompt_tokens: int, completion_tokens: int, duration_ms: int, cost: float | None,
               estimated: bool, conversation_id: str | None, project_id: str | None,
               cached_tokens: int = 0, cache_write_tokens: int = 0, reasoning_tokens: int = 0, tag: str = "", round: int = 0) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO usage_log(id,created_at,model,kind,conversation_id,project_id,prompt_tokens,completion_tokens,duration_ms,cost,estimated,cached_tokens,cache_write_tokens,reasoning_tokens,tag,round) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_id(), now(), model, kind, conversation_id, project_id, int(prompt_tokens), int(completion_tokens), int(duration_ms), cost, 1 if estimated else 0,
                 int(cached_tokens), int(cache_write_tokens), int(reasoning_tokens), tag or "", int(round or 0)),
            )

    def reprice(self, pricing: Pricing, settings: dict[str, Any]) -> int:
        """Recompute cost for every row (after prices change). Returns rows updated."""
        with self.db.tx() as c:
            rows = c.execute("SELECT id, model, prompt_tokens, completion_tokens, cached_tokens, cache_write_tokens FROM usage_log").fetchall()
            n = 0
            for r in rows:
                cost = pricing.cost(settings, r["model"], r["prompt_tokens"], r["completion_tokens"], r["cached_tokens"], r["cache_write_tokens"])
                c.execute("UPDATE usage_log SET cost=? WHERE id=?", (cost, r["id"]))
                n += 1
        return n

    def alert_state(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Spend today and this calendar month against settings["usageAlerts"] (0 = off). Informational only."""
        lim = settings.get("usageAlerts") if isinstance(settings.get("usageAlerts"), dict) else {}
        t = datetime.now()
        starts = {"daily": t.replace(hour=0, minute=0, second=0, microsecond=0), "monthly": t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)}
        out: dict[str, Any] = {}
        with self.db.tx() as c:
            for k, st in starts.items():
                spent = c.execute("SELECT COALESCE(SUM(cost),0) FROM usage_log WHERE created_at >= ?", (st.timestamp(),)).fetchone()[0]
                try:
                    limit = float(lim.get(k + "Cost") or 0)
                except (TypeError, ValueError):
                    limit = 0.0
                out[k] = {"spent": round(spent, 6), "limit": limit, "over": limit > 0 and spent >= limit}
        out["over"] = out["daily"]["over"] or out["monthly"]["over"]
        return out

    def report(self, days: int = 30) -> dict[str, Any]:
        days = max(1, min(int(days), 365))
        start_day = (datetime.now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        since = start_day.timestamp()
        with self.db.tx() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM usage_log WHERE created_at >= ? ORDER BY created_at", (since,)).fetchall()]
            projects = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM projects").fetchall()}

        def bucket() -> dict[str, Any]:
            return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "reasoning_tokens": 0, "cost": 0.0, "unpriced": 0, "ms": 0, "chat_calls": 0, "learn_calls": 0, "other_calls": 0}

        def add(b: dict[str, Any], r: dict[str, Any]) -> None:
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

        daily = {(start_day + timedelta(days=i)).strftime("%Y-%m-%d"): bucket() for i in range(days)}
        hourly = {h: 0 for h in range(24)}
        weekday = {d: 0 for d in range(7)}
        by_model: dict[str, dict[str, Any]] = {}
        by_kind: dict[str, dict[str, Any]] = {}
        by_project: dict[str, dict[str, Any]] = {}
        by_tag: dict[str, dict[str, Any]] = {}
        total = bucket()
        for r in rows:
            t = datetime.fromtimestamp(r["created_at"])
            add(total, r)
            day = t.strftime("%Y-%m-%d")
            if day in daily:
                add(daily[day], r)
            if r["kind"] == "chat":
                hourly[t.hour] += 1
                weekday[t.weekday()] += 1
            add(by_model.setdefault(r["model"], bucket()), r)
            add(by_kind.setdefault(r["kind"], bucket()), r)
            add(by_tag.setdefault(r.get("tag") or "untagged", bucket()), r)
            add(by_project.setdefault(projects.get(r["project_id"], "Personal") if r["project_id"] else "Personal", bucket()), r)

        def finish(b: dict[str, Any]) -> dict[str, Any]:
            return {**b, "cost": round(b["cost"], 6), "avg_ms": int(b["ms"] / b["calls"]) if b["calls"] else 0, "tokens": b["prompt_tokens"] + b["completion_tokens"],
                    "cache_hit_rate": round(b["cached_tokens"] / b["prompt_tokens"], 4) if b["prompt_tokens"] else 0,
                    "reasoning_share": round(b["reasoning_tokens"] / b["completion_tokens"], 4) if b["completion_tokens"] else 0}

        return {
            "days": days,
            "totals": finish(total),
            "daily": [{"day": d, **finish(b)} for d, b in daily.items()],
            "hourly": [{"hour": h, "calls": n} for h, n in hourly.items()],
            "weekday": [{"weekday": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d], "calls": n} for d, n in weekday.items()],
            "by_model": sorted(({"model": m, **finish(b)} for m, b in by_model.items()), key=lambda x: -x["tokens"]),
            "by_kind": [{"kind": k, **finish(b)} for k, b in by_kind.items()],
            "by_tag": sorted(({"tag": t, **finish(b)} for t, b in by_tag.items()), key=lambda x: -x["cost"]),
            "by_project": sorted(({"project": p, **finish(b)} for p, b in by_project.items()), key=lambda x: -x["tokens"]),
        }


def dumps(v: Any) -> str:
    return json.dumps(v)
