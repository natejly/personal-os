"""Model duels (developer mode): one request sent to two models at once, a vote on which reply is better.

`merge` interleaves two model streams, each event tagged with its side. A duel only streams: tool calls come back
on a candidate's `end` event and are shown, never run. The picked candidate's reply re-enters the normal tool loop
(app.py `_chat_stream`), so a side effect happens once, under the usual permission rules. `Votes` keeps the head-to-head
record and `standings` turns it into wins, a simple Elo and mean latency per model.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator

WINNERS = ("a", "b", "tie", "bad")
ELO_START, ELO_K = 1000.0, 32.0
EXCERPT_CHARS = 200


async def merge(streams: dict[str, AsyncIterator[dict[str, Any]]]) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Yield (side, event) from every stream as events arrive. A stream that raises ends with an `error` event."""
    q: asyncio.Queue[tuple[str, dict[str, Any] | None]] = asyncio.Queue()

    async def pump(side: str, s: AsyncIterator[dict[str, Any]]) -> None:
        try:
            async for ev in s:
                await q.put((side, ev))
        except Exception as e:  # noqa: BLE001 - one side failing must not end the other
            await q.put((side, {"type": "error", "message": str(e) or type(e).__name__}))
        finally:
            await q.put((side, None))

    tasks = [asyncio.create_task(pump(side, s)) for side, s in streams.items()]
    live = len(tasks)
    try:
        while live:
            side, ev = await q.get()
            if ev is None:
                live -= 1
            else:
                yield side, ev
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def call_summary(call: dict[str, Any]) -> dict[str, str]:
    """A proposed tool call as the duel column shows it: name and a short argument line."""
    raw = call.get("arguments")
    text = raw if isinstance(raw, str) else json.dumps(raw or {}, ensure_ascii=False)
    return {"name": str(call.get("name") or ""), "args": text[:300]}


def elo(votes: list[dict[str, Any]]) -> dict[str, float]:
    """Elo per model from votes in order. A tie is half a win; `bad` (both bad) moves nobody."""
    r: dict[str, float] = {}
    for v in votes:
        if v["winner"] == "bad" or v["model_a"] == v["model_b"]:
            continue
        ra, rb = r.setdefault(v["model_a"], ELO_START), r.setdefault(v["model_b"], ELO_START)
        sa = {"a": 1.0, "b": 0.0, "tie": 0.5}[v["winner"]]
        ea = 1 / (1 + 10 ** ((rb - ra) / 400))
        r[v["model_a"]], r[v["model_b"]] = ra + ELO_K * (sa - ea), rb + ELO_K * ((1 - sa) - (1 - ea))
    return r


def standings(votes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per model: wins, losses, ties, duels, win rate (a tie counts half), Elo and mean latency, best Elo first."""
    rows: dict[str, dict[str, Any]] = {}
    ms: dict[str, list[int]] = {}
    for v in votes:
        for side, model in (("a", v["model_a"]), ("b", v["model_b"])):
            row = rows.setdefault(model, {"model": model, "wins": 0, "losses": 0, "ties": 0, "bad": 0})
            if v["winner"] == "tie":
                row["ties"] += 1
            elif v["winner"] == "bad":
                row["bad"] += 1
            elif v["winner"] == side:
                row["wins"] += 1
            else:
                row["losses"] += 1
            if (t := v.get(f"{side}_ms")) is not None:
                ms.setdefault(model, []).append(int(t))
    ratings = elo(votes)
    out = []
    for m, row in rows.items():
        n = row["wins"] + row["losses"] + row["ties"]
        out.append({**row, "duels": n + row["bad"], "win_rate": round((row["wins"] + row["ties"] / 2) / n, 3) if n else None,
                    "elo": round(ratings.get(m, ELO_START)), "mean_ms": round(sum(ms[m]) / len(ms[m])) if m in ms else None})
    return sorted(out, key=lambda r: -r["elo"])


class Votes:
    def __init__(self, db: Any):
        self.db = db

    def add(self, *, conversation_id: str, model_a: str, model_b: str, winner: str, prompt: str,
            a_ms: int | None, b_ms: int | None, a_tokens: int | None, b_tokens: int | None) -> None:
        if winner not in WINNERS:
            raise ValueError(f"winner must be one of {WINNERS}")
        with self.db.tx() as c:
            c.execute("INSERT INTO model_votes(created_at, conversation_id, model_a, model_b, winner, prompt_excerpt, a_ms, b_ms, a_tokens, b_tokens)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?)", (time.time(), conversation_id, model_a, model_b, winner, prompt[:EXCERPT_CHARS],
                                                        a_ms, b_ms, a_tokens, b_tokens))

    def all(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            return [dict(r) for r in c.execute("SELECT * FROM model_votes ORDER BY created_at, id")]

    def standings(self) -> dict[str, Any]:
        votes = self.all()
        return {"votes": len(votes), "models": standings(votes)}

    def reset(self) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM model_votes")
