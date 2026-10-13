"""Score the pre-round-1 tool load (mcp_search.preselect vs preselect_ranked) on labeled messages.

    cd backend && uv run --with pytest python scripts/tool_preselect_eval.py [--live] [--sweep] [--verbose]
                  [--data-dir DIR] [--model NAME]

The catalog is the real one: every built-in tool the chat loop would hold back (tests' full_toolbox, minus the chat core),
through mcp_search.build_docs. Each message either needs one of a set of tools (a hit is any of them loaded) or none
(chit-chat: any load is a false load). BM25 always runs. --live also runs the reranker (fireworks key read-only from the
data dir, never printed) and times each call; --sweep prints hit / false-load rates over a grid of thresholds from the
recorded top scores, to pick PRESELECT_RANK_MIN / PRESELECT_RANK_NEAR.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from personal_os import mcp_search, provider_keys, providers, retrieval_rerank, tools  # noqa: E402
from personal_os.secrets import SecretStore  # noqa: E402
from graph_eval import DEFAULT_DATA_DIR, read_settings  # noqa: E402
from test_mcp_servers import full_toolbox  # noqa: E402

# (message, tools any of which satisfies it); an empty set is a message that should load nothing.
CASES: list[tuple[str, set[str]]] = [
    ("move my 3pm to Thursday", {"calendar_update"}),
    ("what do I have on tomorrow afternoon, am I free for a call", {"calendar_free_busy", "calendar_find_time"}),
    ("cancel the dentist appointment on Friday", {"calendar_delete"}),
    ("set up lunch with Priya next week", {"calendar_create", "calendar_find_time"}),
    ("tell the organizer I'll be there for the standup", {"calendar_respond"}),
    ("what did Sam email me about the contract", {"gmail_read", "mail_followups"}),
    ("write back to Dana saying the invoice is paid", {"gmail_send", "gmail_draft"}),
    ("archive everything from the newsletter", {"gmail_modify"}),
    ("who still owes me an answer by email", {"mail_followups"}),
    ("remind me to renew the passport", {"todo_add", "google_tasks_add"}),
    ("tick off the groceries item", {"todo_update", "google_tasks_complete"}),
    ("how many hours did I sleep this week", {"health_summary"}),
    ("I drank two glasses of water", {"health_log"}),
    ("find the quarterly numbers spreadsheet in my drive", {"google_drive_search", "google_docs_search"}),
    ("add a row to the budget sheet with rent 1200", {"google_sheets_write"}),
    ("grab the text of this article https://example.com/post", {"fetch_url", "open_page"}),
    ("what is the transcript of this video https://youtube.com/watch?v=abc", {"youtube_video"}),
    ("any open issues in the openai/whisper repo", {"github_search", "github_read"}),
    ("run this script and plot the results", {"run_python"}),
    ("look for the tax pdf somewhere on my laptop", {"find_files"}),
    ("every Monday at 8 summarize my inbox for me", {"schedule_task"}),
    ("jot down my meeting notes as a new file", {"doc_create"}),
    ("find the doc where I wrote about the launch plan", {"doc_search", "doc_list"}),
    ("let a coding agent fix the failing tests in my repo", {"coding_session_start", "opencode_run"}),
    ("draw me a picture of a fox in a snowstorm", {"generate_image"}),
    ("hey, how are you today", set()),
    ("thanks, that was helpful", set()),
    ("what is the capital of Australia", set()),
    ("explain how a hash map works", set()),
    ("give me three ideas for a birthday present for my dad", set()),
    ("rewrite this sentence to sound friendlier: send me the report", set()),
    ("what's the difference between affect and effect", set()),
    ("translate good morning into Spanish", set()),
    ("can you summarize what we just discussed", set()),
    ("I'm feeling a bit stressed about work", set()),
    ("what's 15 percent of 240", set()),
    ("tell me a short joke", set()),
    ("is it better to rent or buy in general", set()),
    ("help me decide between two job offers", set()),
    ("why is the sky blue", set()),
]


def catalog() -> list[mcp_search.Doc]:
    tb = full_toolbox()
    return mcp_search.build_docs([{"slug": n, "description": s.description, "parameters": s.parameters, "server": s.group}
                                  for n, s in sorted(tb.specs.items()) if not tools.is_core(s)])


def score(cases: list[tuple[str, set[str]]], picks: list[list[str]]) -> tuple[float, float]:
    need = [(want, p) for (_, want), p in zip(cases, picks) if want]
    none = [p for (_, want), p in zip(cases, picks) if not want]
    return (sum(bool(set(p) & want) for want, p in need) / len(need), sum(bool(p) for p in none) / len(none))


_REAL = retrieval_rerank.rerank_scores


class Recorder:
    """Stands in for retrieval_rerank.rerank_scores: the real call, timed, its scored head kept per query."""

    def __init__(self, docs: list[mcp_search.Doc]) -> None:
        self.docs = docs
        self.ms: list[float] = []
        self.seen: dict[str, list[tuple[str, float]]] = {}
        self.fails = 0

    async def __call__(self, settings, model, query, docs, **kw):  # noqa: ANN001
        by_text = {d.text or d.slug: d.slug for d in self.docs}
        t = time.perf_counter()
        try:
            out = await _REAL(settings, model, query, docs, **kw)
        except Exception:
            self.fails += 1
            raise
        finally:
            self.ms.append((time.perf_counter() - t) * 1000)
        self.seen[query] = [(by_text[docs[i]], sc) for i, sc in (out or [])]
        return out


def live_settings(data_dir: Path, model: str) -> dict[str, Any]:
    s = read_settings(data_dir)
    key = provider_keys.load(SecretStore(data_dir)).get("fireworks", "")
    if not key:
        raise SystemExit("no fireworks key in the data dir")
    return {**s, "provider": "fireworks", "baseUrl": "https://api.fireworks.ai/inference/v1", "apiKey": key,
            "retrievalRerankModel": model or ""}


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--model", default="")
    ap.add_argument("--data-dir", default=os.environ.get("PERSONAL_OS_DATA_DIR") or DEFAULT_DATA_DIR)
    a = ap.parse_args()
    docs = catalog()
    print(f"{len(docs)} held-back tools, {sum(bool(w) for _, w in CASES)} need one, {sum(not w for _, w in CASES)} need none")
    bm = [mcp_search.preselect(docs, m) for m, _ in CASES]
    hit, false = score(CASES, bm)
    print(f"{'bm25':10} hit {hit:.0%}  false-load {false:.0%}")
    if not a.live:
        return
    settings = live_settings(Path(a.data_dir).expanduser(), a.model)
    print("rerank model", providers.rerank_model(settings))
    mcp_search.PRESELECT_TIMEOUT = float(os.environ.get("EVAL_TIMEOUT", mcp_search.PRESELECT_TIMEOUT))
    rec = Recorder(docs)
    retrieval_rerank.rerank_scores = rec  # the module attribute preselect_ranked calls
    ranked = []
    for m, _ in CASES:
        mcp_search._rank_down_until = 0.0
        ranked.append(await mcp_search.preselect_ranked(docs, m, settings))
    hit, false = score(CASES, ranked)
    xs = sorted(rec.ms)
    pct = lambda p: xs[min(len(xs) - 1, int(p * len(xs)))]  # noqa: E731 - nearest rank
    print(f"{'reranked':10} hit {hit:.0%}  false-load {false:.0%}  (min {mcp_search.PRESELECT_RANK_MIN}, near "
          f"{mcp_search.PRESELECT_RANK_NEAR})  calls {len(xs)} fails {rec.fails}  p50 {pct(.5):.0f} ms  p95 {pct(.95):.0f} ms")
    if a.verbose:
        for (m, want), r, b in zip(CASES, ranked, bm):
            print(f"  want {sorted(want) or '-'}  rerank {r}  bm25 {b}  | {m}")
            top = rec.seen.get(m[:mcp_search.PRESELECT_CHARS], [])[:3]
            print("     top", [(sl, round(sc, 3)) for sl, sc in top])
    if a.sweep:
        print("min   near  hit   false-load")
        for lo in (0.002, 0.005, 0.01, 0.02, 0.03, 0.05, 0.1, 0.2, 0.3):
            for near in (0.0, 0.5, 0.8):
                picks = []
                for m, _ in CASES:
                    sc = rec.seen.get(m[:mcp_search.PRESELECT_CHARS], [])[:mcp_search.PRESELECT_LIMIT]
                    top = sc[0][1] if sc else 0
                    picks.append([sl for sl, v in sc if v >= lo and v >= near * top])
                h, f = score(CASES, picks)
                print(f"{lo:<5} {near:<5} {h:.0%}  {f:.0%}")


if __name__ == "__main__":
    asyncio.run(main())
