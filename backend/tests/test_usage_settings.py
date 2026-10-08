"""Settings → Usage: today/week/month totals, by-feature and merged by-model breakdowns, alias pricing for a provider
called directly (Fireworks ids), re-pricing that never erases a known cost, embedding rows, and GET /usage. Offline.

Run: backend/.venv/bin/python -m pytest backend/tests/test_usage_settings.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="usageset-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import embed, llm  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.usage import FEATURES, Pricing, Usage, feature, period_starts, short_model  # noqa: E402

FW = "accounts/fireworks/models/ember-1"


def _fresh() -> tuple[Database, Usage]:
    db = Database(Path(tempfile.mkdtemp(prefix="usageset-db-")))
    return db, Usage(db)


def _rec(u: Usage, model: str = "m", kind: str = "chat", tag: str = "", cost: float | None = 0.1, pt: int = 100, ct: int = 10, cached: int = 0) -> None:
    u.record(model=model, kind=kind, prompt_tokens=pt, completion_tokens=ct, duration_ms=5, cost=cost, estimated=False,
             conversation_id=None, project_id=None, cached_tokens=cached, tag=tag)


def _backdate(db: Database, t: datetime) -> None:
    with db.tx() as c:
        c.execute("UPDATE usage_log SET created_at=? WHERE id=(SELECT id FROM usage_log ORDER BY rowid DESC LIMIT 1)", (t.timestamp(),))


def test_short_model_and_feature() -> None:
    assert short_model(FW) == "ember-1" and short_model("ember-1") == "ember-1" and short_model("fireworks_ai/glm-5.3") == "glm-5.3"
    assert feature("chat", "chat") == "chat" and feature("chat", "") == "chat"
    assert feature("chat", "job:j1") == "jobs" and feature("chat", "desk:d1") == "desks"
    assert feature("learn", "") == "memory" and feature("other", "graph-backfill") == "memory"
    assert feature("embedding", "chat") == "embeddings" and feature("recall_query", "") == "embeddings"
    assert feature("title", "") == "helpers" and feature("review", "chat") == "helpers"
    assert feature("recall_index", "", "accounts/fireworks/models/qwen3-embedding-8b") == "embeddings"
    assert feature("vision", "") == "vision" and feature("whatever", "") == "other"


def test_periods_week_and_month() -> None:
    db, u = _fresh()
    at = datetime(2026, 10, 7, 15, 0)  # a Wednesday
    starts = period_starts(at)
    assert starts["week"] == datetime(2026, 10, 5) and starts["month"] == datetime(2026, 10, 1) and starts["today"] == datetime(2026, 10, 7)
    _rec(u, pt=1, ct=1); _backdate(db, at - timedelta(hours=1))           # today
    _rec(u, pt=10, ct=1); _backdate(db, datetime(2026, 10, 5, 9))          # Monday: week + month
    _rec(u, pt=100, ct=1); _backdate(db, datetime(2026, 10, 2, 9))         # last week, this month
    _rec(u, pt=1000, ct=1); _backdate(db, datetime(2026, 9, 30, 9))        # last month
    p = u.periods(at)
    assert (p["today"]["prompt_tokens"], p["week"]["prompt_tokens"], p["month"]["prompt_tokens"]) == (1, 11, 111)
    assert p["week"]["since"] == "2026-10-05" and p["month"]["calls"] == 3
    # The report carries the periods whatever its range is.
    assert u.report(1, at=at)["periods"]["month"]["prompt_tokens"] == 111


def test_by_model_merges_aliases_and_by_feature() -> None:
    _, u = _fresh()
    _rec(u, model="ember-1", tag="chat", cost=0.5)
    _rec(u, model=FW, tag="chat", cost=None, cached=40)
    _rec(u, model="deepseek-v4-flash", kind="learn")
    _rec(u, model="accounts/fireworks/models/qwen3-embedding-8b", kind="embedding", ct=0, cost=None)
    _rec(u, model=FW, tag="job:j1")
    r = u.report(7)
    models = {m["model"]: m for m in r["by_model"]}
    assert set(models) == {"ember-1", "deepseek-v4-flash", "qwen3-embedding-8b"}
    e = models["ember-1"]
    assert e["calls"] == 3 and e["ids"] == sorted(["ember-1", FW]) and e["unpriced"] == 1 and e["cached_tokens"] == 40
    feats = [f["feature"] for f in r["by_feature"]]
    assert feats == [k for k in FEATURES if k in {"chat", "jobs", "memory", "embeddings"}], feats
    assert {f["feature"]: f["label"] for f in r["by_feature"]}["embeddings"] == "Embeddings"
    assert r["totals"]["unpriced"] == 2, "an unpriced call is counted as unpriced, not as $0"


def test_alias_pricing_and_reprice_keeps_known_costs() -> None:
    db, u = _fresh()
    pricing = Pricing()
    cfg = {"modelPrices": {"ember-1": {"input": 1.0, "output": 2.0}}}
    assert pricing.cost(cfg, FW, 1_000_000, 1_000_000) == 3.0, "a short-name price covers the provider's full id"
    assert pricing.cost({"modelPrices": {FW: {"input": 1.0, "output": 0}}}, "ember-1", 1_000_000, 0) == 1.0
    assert pricing.cost({"modelPrices": {}}, "accounts/fireworks/models/minimax-m3", 10, 10) is None, "no price is unknown, never a guess"
    assert pricing.cost({"modelPrices": {}}, FW, 1_000_000, 0) == 3.0, "the Fireworks list price covers the full id"

    _rec(u, model="mystery-model", cost=0.42)  # priced by a proxy that is gone; no price now
    _rec(u, model=FW, cost=None, pt=1_000_000, ct=0)
    n = u.reprice(pricing, cfg)
    with db.tx() as c:
        costs = {r["model"]: r["cost"] for r in c.execute("SELECT model, cost FROM usage_log")}
    assert n == 1 and costs["mystery-model"] == 0.42 and costs[FW] == 1.0


def test_embedding_calls_are_logged() -> None:
    seen: list[dict] = []
    llm.on_usage(seen.append)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}], "usage": {"prompt_tokens": 7, "total_tokens": 7}})

    real = httpx.AsyncClient
    embed.httpx.AsyncClient = lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)})  # type: ignore[assignment]
    try:
        out = asyncio.run(embed.embed_texts({"baseUrl": "https://api.fireworks.ai/inference/v1", "apiKey": "k"}, ["hello"],
                                            "accounts/fireworks/models/qwen3-embedding-8b"))
    finally:
        embed.httpx.AsyncClient = real  # type: ignore[assignment]
        llm._usage_listeners.remove(seen.append)
    assert out == [[0.1, 0.2]]
    rows = [r for r in seen if r["kind"] == "embedding"]
    assert len(rows) == 1 and rows[0]["prompt_tokens"] == 7 and rows[0]["completion_tokens"] == 0 and not rows[0]["estimated"]


def test_usage_endpoint_shape() -> None:
    appmod.usage.record(model=FW, kind="chat", prompt_tokens=50, completion_tokens=5, duration_ms=3, cost=None, estimated=False,
                        conversation_id=None, project_id=None, tag="chat")
    client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
    r = client.get("/usage?days=30")
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("periods", "by_model", "by_feature", "daily", "totals", "prices"):
        assert k in body, k
    assert set(body["periods"]) == {"today", "week", "month"} and body["periods"]["today"]["calls"] >= 1
    assert "alerts" not in body, "usage is information only"
