"""deep_research: plan sub-questions, fan researchers out as read-only subagents, then reconcile one cited answer.

The researchers are ordinary subagents (subagents.py), so concurrency, depth, budget roll-up and Stop all behave as
they do for agent_spawn. This module only plans, reads their JSON reports, and writes the final answer. Sources go
through the shared citation registry (tools._cite), so [n] chips and the sources list work unchanged. The trail
(plan, steps, sources considered, dropped claims) rides on the tool event under `research`; the model never sees it.
"""
from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
from typing import Any

from . import llm, redact
from .tools import ToolSpec, _cite, _obj, tool_error, web_ref

RESEARCH_TOOLS = ["web_search", "fetch_url", "read_feed", "youtube_video", "github_read"]
MAX_STEPS = {"normal": 3, "deep": 6}
MAX_GAPS = 3  # follow-up researchers in a deep run's second pass

PLAN_PROMPT = ("Split the research question into {n} independent sub-questions that together cover it, each answerable with web searches. "
               "Reply with only JSON: {{\"questions\": [\"...\"]}}.")
RESEARCHER_TASK = ("You are one researcher on a larger question. Overall question: {question}\nYour sub-question: {sub}\n\n"
                   "Search the web and open the most relevant pages. Finish with only a JSON object: "
                   "{{\"claims\": [{{\"claim\": \"one factual statement\", \"url\": \"the page you read it on\", \"title\": \"page title\"}}], "
                   "\"gaps\": \"what you could not find\"}}. Give 3 to 8 claims. Every claim needs the URL of a page you actually read; "
                   "leave out anything you cannot source.")
GAP_PROMPT = ("Question: {question}\n\nClaims so far:\n{claims}\n\nName up to {n} follow-up sub-questions that fill what is missing, thin or "
              "contradictory. Reply with only JSON: {{\"questions\": [\"...\"]}}, an empty list if the claims already answer it.")
LEAD_PROMPT = ("You write the final answer to a research question from numbered claims gathered by researchers. Use only these claims. "
               "Put the claim's number in brackets, like [3], after each sentence that relies on it. Where claims disagree, say so and cite both. "
               "Leave out claims that are off-topic or contradicted by better-supported ones. State nothing without a number. "
               "If the evidence is thin, say what is missing. Be direct; no preamble.")


def _json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _questions(text: str, limit: int) -> list[str]:
    qs = _json(text).get("questions")
    return [" ".join(q.split())[:300] for q in qs if isinstance(q, str) and q.strip()][:limit] if isinstance(qs, list) else []


def _valid_url(u: Any) -> bool:
    if not isinstance(u, str):
        return False
    p = urllib.parse.urlsplit(u.strip())
    return p.scheme in ("http", "https") and bool(p.netloc)


def claims_from(report: str) -> tuple[list[dict[str, str]], int]:
    """-> (claims that name a real page, how many claims named none). A claim without a source is dropped."""
    raw = _json(report).get("claims")
    good: list[dict[str, str]] = []
    dropped = 0
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict) or not str(c.get("claim") or "").strip():
            continue
        if _valid_url(c.get("url")):
            good.append({"claim": " ".join(str(c["claim"]).split())[:500], "url": str(c["url"]).strip(),
                         "title": " ".join(str(c.get("title") or "").split())[:200]})
        else:
            dropped += 1
    return good, dropped


async def run(tb: Any, ctx: dict[str, Any], question: str, depth: str = "normal", focus: str = "") -> Any:
    sub = getattr(tb, "subagents", None)
    if sub is None:
        return tool_error("Subagents are not available, so deep_research cannot run.")
    question = str(question or "").strip()
    if not question:
        return tool_error("deep_research needs a question.", field="question", example={"question": "What changed in Python 3.14 packaging?"})
    depth = depth if depth in MAX_STEPS else "normal"
    settings = ctx.get("settings") or tb.settings()
    model = str(ctx.get("model") or settings.get("defaultModel") or "")
    effort, stop = str(ctx.get("effort") or "default"), ctx.get("stop")

    async def ask(system: str, user: str) -> str:
        return await llm.complete(settings, model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                  effort=effort, cancel=stop)

    q = redact.scrub_command_output(question)[:2000]
    f = redact.scrub_command_output(str(focus or ""))[:500]
    n = MAX_STEPS[depth]
    plan = _questions(await ask(PLAN_PROMPT.format(n=n), f"Question: {q}" + (f"\nFocus: {f}" if f else "")), n) or [question[:300]]

    steps: list[dict[str, Any]] = []
    claims: list[dict[str, str]] = []
    dropped = 0
    sem = asyncio.Semaphore(max(1, sub._int("subagentMaxConcurrent") - len(sub.running())))

    async def researcher(step: dict[str, Any]) -> None:
        nonlocal dropped
        async with sem:
            if stop is not None and stop.is_set():
                step["status"] = "skipped"
                return
            out = await sub.spawn_tool(ctx, task=RESEARCHER_TASK.format(question=q, sub=step["q"]), role="researcher", tools=RESEARCH_TOOLS)
        got, lost = claims_from(out.get("report") or "") if isinstance(out, dict) else ([], 0)
        dropped += lost
        claims.extend(got)
        seen: dict[str, str] = {}
        for c in got:
            seen.setdefault(c["url"], c["title"])
        step.update(status="done" if got else "failed", claims=len(got), sources=[{"url": u, "title": t} for u, t in seen.items()])

    async def one_pass(qs: list[str]) -> None:
        new = [{"q": x, "status": "running", "sources": [], "claims": 0} for x in qs]
        steps.extend(new)
        await asyncio.gather(*(researcher(s) for s in new))

    await one_pass(plan)
    if depth == "deep" and claims and not (stop is not None and stop.is_set()):
        listing = "\n".join(f"- {c['claim']} ({c['url']})" for c in claims)[:12000]
        gaps = _questions(await ask("You review research in progress.", GAP_PROMPT.format(question=q, claims=listing, n=MAX_GAPS)), MAX_GAPS)
        if gaps:
            await one_pass(gaps)
            plan = plan + gaps
    if not claims:
        return tool_error("deep_research found no sourced claims; try searching directly.")

    numbered: dict[str, int] = {}
    titles: dict[str, str] = {}
    for c in claims:
        numbered.setdefault(c["url"], _cite(ctx, web_ref(c["url"], c["title"], c["claim"])))
        titles.setdefault(c["url"], c["title"])
    listing = "\n".join(f"[{numbered[c['url']]}] {c['claim']} (source: {c['title'] or c['url']})" for c in claims)[:24000]
    answer = (await ask(LEAD_PROMPT, f"Question: {q}\n\nClaims:\n{listing}")).strip()
    used = sorted({int(x) for x in re.findall(r"\[(\d+)\]", answer)} & set(numbered.values()))
    trail = {"plan": plan, "steps": steps, "dropped": dropped,
             "sources_considered": [{"url": u, "title": titles[u], "n": k} for u, k in numbered.items()]}
    return {"answer": answer, "citations": used, "research": trail,
            "note": "Present this answer to the user as written, keeping the [n] numbers; do not add claims that have no number."}


def register(tb: Any) -> None:
    async def deep_research(ctx: dict[str, Any], question: str, depth: str = "normal", focus: str = "") -> Any:
        return await run(tb, ctx, question, depth, focus)

    tb.specs["deep_research"] = ToolSpec(
        "deep_research",
        "Answer a question that needs many searches: plans sub-questions, runs several read-only researchers in parallel, and returns one "
        "answer with [n] source numbers (keep them in your reply). Use it for broad or contested questions; use web_search for a quick fact. "
        "depth 'normal' runs up to 3 researchers, 'deep' up to 6 plus a second pass for gaps and costs more. focus narrows the angle.",
        _obj({"question": {"type": "string"}, "depth": {"type": "string", "enum": ["normal", "deep"], "default": "normal"},
              "focus": {"type": "string", "description": "Optional angle or constraint"}}, ["question"]),
        deep_research, "research", "network", taints=True,
        examples=[{"question": "What changed in Python 3.14 packaging?"}, {"question": "State of solid-state batteries in 2026", "depth": "deep"}])
