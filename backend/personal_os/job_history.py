"""A job's run history, derived from rows: pure functions over agent_runs / run_events / proposals.

Nothing here reads a model's words to decide anything. `summary` is shown as text and never parsed; status,
duration, cost, attempt and counts all come from the run row, its budget snapshot and the event/proposal tallies.
The notification events carry names and counts only: a reply can quote mail, and a notification banner is
readable from a locked screen.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import statistics
from typing import Any, Iterable

from .jobs import retryable

FAILED = ("error", "interrupted", "timed_out")
SUMMARY_CHARS = 400
NOTIFY_CAP = 20


# Tokens that change every run without the result changing: ISO dates and times, clock times, epoch seconds.
_STAMPS = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?"
                     r"|\b\d{1,2}:\d{2}(?::\d{2})?\s?(?:[ap]m\b)?|\b\d{10}(?:\d{3})?\b", re.I)


def result_digest(text: str) -> str:
    """A stable fingerprint of a run's result: timestamp-looking tokens dropped, whitespace and case folded.
    "" for an empty result, which is never compared (nothing to say it did not change)."""
    body = " ".join(_STAMPS.sub(" ", text or "").lower().split())
    return hashlib.sha256(body.encode()).hexdigest()[:16] if body else ""


def _timed_out(budget: Any, error: str | None) -> bool:
    """The run hit its wall-clock cap: the budget snapshot says it used all of `max_seconds`."""
    if isinstance(budget, dict):
        lim, used = budget.get("max_seconds"), budget.get("seconds")
        if isinstance(lim, (int, float)) and lim > 0 and isinstance(used, (int, float)) and used >= lim * 0.999:
            return True
    return bool(error) and "Out of budget (time" in (error or "")


def summarize_run(run: dict[str, Any], event_counts: dict[str, int] | None = None,
                  proposal_counts: dict[str, int] | None = None, summary: str = "") -> dict[str, Any]:
    inp = run.get("input") if isinstance(run.get("input"), dict) else {}
    budget = run.get("budget") if isinstance(run.get("budget"), dict) else None
    ended = run.get("ended_at")
    status = run.get("status") or "running"
    if status in ("awaiting_approval",):
        status = "running"
    if ended is not None and _timed_out(budget, run.get("error")):
        status = "timed_out"
    mine = proposal_counts or {}
    dur = (float(ended) - float(run["started_at"])) if ended is not None else None
    return {
        "run_id": run["run_id"], "conversation_id": run.get("conversation_id"), "status": status,
        "job_id": inp.get("job_id"), "job": inp.get("job"),
        "started_at": run["started_at"], "ended_at": ended,
        "duration_s": round(dur, 3) if dur is not None else None,
        "due_at": inp.get("due_at"), "late": bool(inp.get("late")), "missed_slots": int(inp.get("missed_slots") or 0),
        "attempt": int(inp.get("attempt") or 1), "retry_of": inp.get("retry_of"),
        "manual": bool(inp.get("manual")), "dry_run": bool(inp.get("dry_run")), "test": bool(inp.get("test")),
        # The result matched the previous run's on a job set to notify only on change (see jobs_policy.settle).
        "unchanged": bool(inp.get("unchanged")),
        "tool_calls": (event_counts or {}).get("tool_result", 0),
        "proposals": {k: int(mine.get(k, 0)) for k in ("pending", "accepted", "rejected")},
        "cost": budget.get("cost") if budget and budget.get("cost") is not None else None,
        "error": run.get("error"),
        "summary": summary[:SUMMARY_CHARS],
    }


def skip_record(row: dict[str, Any]) -> dict[str, Any]:
    """A job_skips row as a history line: status 'skipped', placed by when the slot was turned away."""
    return {"run_id": f"skip:{row['id']}", "conversation_id": None, "status": "skipped", "reason": row["reason"],
            "due_at": row.get("due_at"), "started_at": row["at"]}


def merge_skips(runs: list[dict[str, Any]], skips: Iterable[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Runs and skip lines together, newest first, `limit` in all."""
    return sorted([*runs, *(skip_record(k) for k in skips)], key=lambda r: r["started_at"], reverse=True)[:limit]


def stats(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Health of a job over a set of summarised runs. Runs still going are not counted either way."""
    done = [r for r in rows if r["status"] != "running" and not r.get("dry_run")]
    ok = [r for r in done if r["status"] == "done"]
    failed = [r for r in done if r["status"] in FAILED]
    durs = [r["duration_s"] for r in done if r["duration_s"] is not None]
    costs = [r["cost"] for r in done if r["cost"] is not None]
    return {
        "runs": len(done), "ok": len(ok), "failed": len(failed),
        "success_rate": round(len(ok) / len(done), 4) if done else None,
        "median_duration_s": round(statistics.median(durs), 3) if durs else None,
        "last_ok_at": max((r["ended_at"] for r in ok if r["ended_at"] is not None), default=None),
        "total_cost": round(sum(costs), 6) if costs else 0.0,
    }


CSV_COLUMNS = ("run_id", "status", "started_at", "ended_at", "duration_s", "attempt", "manual", "late", "tool_calls",
               "proposals_pending", "proposals_accepted", "proposals_rejected", "cost", "error")


def _cell(v: Any) -> Any:
    # A spreadsheet reads a leading = + - @ as a formula; error text is not ours to trust.
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return "" if v is None else v


def to_csv(rows: Iterable[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for r in rows:
        p = r["proposals"]
        w.writerow([_cell(x) for x in (r["run_id"], r["status"], r["started_at"], r["ended_at"], r["duration_s"], r["attempt"],
                                       r["manual"], r["late"], r["tool_calls"], p["pending"], p["accepted"], p["rejected"],
                                       r["cost"], r["error"])])
    return buf.getvalue()


def _run_target(r: dict[str, Any]) -> str:
    """Where clicking the notification goes: the run's own conversation, or the inbox if it has none."""
    cid = r.get("conversation_id")
    return f"run:{cid}" if cid else "inbox"


def notify_events(runs: Iterable[dict[str, Any]], jobs: dict[str, dict[str, Any]], pending: Iterable[dict[str, Any]],
                  since: float, cap: int = NOTIFY_CAP) -> list[dict[str, Any]]:
    """Compact OS-notification events newer than `since`. Titles and bodies hold names and counts only.

    `runs` are summarised job runs each carrying `job_id`; `jobs` maps id -> job row; `pending` is the pending
    proposals. A failed run only notifies when nothing will retry it (a retry that succeeds is not news).
    Each job's `notify` decides the rest: 'problems' (default) is failures, pauses and proposals; 'always' adds a
    plain successful run; 'never' is silent. Every event carries a `target` for the click: 'inbox' or 'run:<cid>'.
    """
    out: list[dict[str, Any]] = []
    covered: set[str | None] = set()

    def mode(job_id: Any) -> str:
        return (jobs.get(job_id or "") or {}).get("notify") or "problems"

    for r in runs:
        if r.get("dry_run") or r["ended_at"] is None or r["ended_at"] <= since:
            continue
        if r.get("unchanged"):
            covered.add(r["run_id"])
            continue
        job = jobs.get(r.get("job_id") or "") or {}
        want = mode(r.get("job_id"))
        if want == "never":
            covered.add(r["run_id"])
            continue
        name = job.get("name") or r.get("job") or "A scheduled job"
        pend = r["proposals"]["pending"]
        if r["status"] in FAILED:
            final = r["manual"] or r["attempt"] > int(job.get("max_retries") or 0) or not (retryable(job) if job else True)
            if final:
                out.append({"id": f"run:{r['run_id']}:error", "kind": "job_failed", "title": f"{name} failed",
                            "body": "The run did not finish." + (f" Attempt {r['attempt']}." if r["attempt"] > 1 else ""),
                            "at": r["ended_at"], "target": _run_target(r)})
        elif r["status"] == "done" and pend:
            covered.add(r["run_id"])
            out.append({"id": f"run:{r['run_id']}:done", "kind": "job_done_with_proposals", "title": f"{name} finished",
                        "body": f"{pend} proposal{'s' if pend != 1 else ''} waiting for you.", "at": r["ended_at"],
                        "target": "inbox"})
        elif r["status"] == "done" and want == "always":
            out.append({"id": f"run:{r['run_id']}:done", "kind": "job_done", "title": f"{name} finished",
                        "body": "The run finished.", "at": r["ended_at"], "target": _run_target(r)})
    for j in jobs.values():
        if mode(j["id"]) == "never":
            continue
        if not j.get("enabled") and j.get("paused_reason") and (j.get("updated_at") or 0) > since:
            body = ("Its schedule expired. Switch it back on to keep it running." if j["paused_reason"] == "expired"
                    else f"{j.get('consecutive_failures') or 0} failed runs in a row.")
            out.append({"id": f"job:{j['id']}:paused:{int(j['updated_at'])}", "kind": "job_paused",
                        "title": f"{j['name']} was paused", "body": body, "at": j["updated_at"], "target": "inbox"})
    for p in pending:
        if p["created_at"] > since and p.get("run_id") not in covered and p.get("job_id") and mode(p["job_id"]) != "never":
            name = (jobs.get(p["job_id"]) or {}).get("name") or "A scheduled job"
            out.append({"id": f"proposal:{p['id']}", "kind": "proposal_pending", "title": f"{name} has a proposal",
                        "body": "One action is waiting for your approval.", "at": p["created_at"], "target": "inbox"})
    out.sort(key=lambda e: e["at"])
    return out[-cap:]
