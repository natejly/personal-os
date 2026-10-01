"""propose_plan: plan-level approval, bound to argument digests.

A wall of sequential "allow this gmail_send?" modals is approval theatre -- people click through it. So the
model proposes the consequential calls it intends to make, with their concrete arguments, and the user answers
one card: accept, edit, or reject.

This is an *approval* artifact, not a progress checklist. A plan says nothing about what the model has done or
what it means to do next; it is a record of what the user authorised. Nothing here is working memory.

The security property is the digest binding. Each step stores args_digest -- runs.args_digest, the exact
canonicalisation the approvals table already uses -- over the arguments shown on the card. When a step later
executes, the digest of the *actual* arguments is recomputed and claimed against the approved steps:

  * a match is consumed (single use): an approved gmail_send to one recipient cannot authorise a second send;
  * anything else -- a different recipient, one changed character, an extra call, a replay of a consumed step --
    finds nothing and falls back to the per-call approval gate;
  * a `forced` approval (untrusted content upgraded the tool to ask) never consults a plan at all, and a claim
    only matches inside the run the plan was approved in.

Editing a step re-derives its digest from the edited arguments, so what the user saw is what is authorised.
"""
from __future__ import annotations

import json
from typing import Any

from .db import Database, new_id, now, row_to_dict
from .runs import args_digest

# One card has to stay readable, and the point is that the user actually reads it.
MAX_STEPS = 10
MAX_TITLE = 120
MAX_WHY = 200

PLAN_TOOL = "propose_plan"

PLAN_DESCRIPTION = (
    "Ask the user to approve several consequential actions at once, instead of one approval modal per call. "
    "Use it when a request needs two or more actions that reach outside the app (sending mail, creating or "
    "changing calendar events, writing to Google) -- list every one of them, with the exact arguments you will "
    "pass. The user sees one card and can accept, edit, or reject it. Approval is bound to those argument "
    "values: afterwards call each step once, with byte-identical arguments. Anything you change, add, or repeat "
    "asks the user again, so do not propose placeholders you intend to fill in later."
)

PLAN_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "One line naming what the whole plan accomplishes"},
        "steps": {
            "type": "array",
            "maxItems": MAX_STEPS,
            "description": "The consequential calls to authorise, in the order you will make them",
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "description": "Name of the tool this step calls"},
                    "arguments": {"type": "object", "description": "The exact arguments this step will be called with"},
                    "why": {"type": "string", "description": "One short line: why this step"},
                },
                "required": ["tool", "arguments"],
            },
        },
    },
    "required": ["steps"],
}

PLAN_EXAMPLES = [{"title": "Reply to the three interview requests",
                  "steps": [{"tool": "gmail_send", "arguments": {"to": "ana@example.com", "subject": "Re: Thursday",
                                                                 "body": "Thursday at 10 works. See you then."},
                             "why": "accept Ana's slot"},
                            {"tool": "calendar_create", "arguments": {"summary": "Interview: Ana", "start": "2026-10-02T10:00:00",
                                                                      "end": "2026-10-02T11:00:00"},
                             "why": "hold the time"}]}]

APPROVED_NOTE = ("Approved. Call each approved step exactly once, with exactly these arguments -- the approval is bound "
                 "to these values. A changed argument, an extra call, or a repeat asks the user again.")
REJECTED_ERROR = ("The user rejected this plan. Do not run any of its steps. Say in one line what you were going to do "
                  "and ask what they would like instead.")
DROPPED_NOTE = "The user removed these steps from the plan; they are not authorised."
EDITED_NOTE = "The user edited these arguments. Use the arguments below, not the ones you proposed."


def normalize_plan(args: dict[str, Any], modes: dict[str, str] | None = None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Canonical (title, steps) for a propose_plan call, or (args, error) when it cannot be shown to a user.

    Validation happens before the card: a plan naming a tool that does not exist, or that is off in this chat,
    would be approved and then fail, which teaches the user that approving means nothing.
    """
    from .tools import tool_error  # local: tools.py imports this module for the tool's schema

    raw = args.get("steps")
    if not isinstance(raw, list) or not raw:
        return args, tool_error("propose_plan needs a non-empty 'steps' array.", field="steps",
                                expected="a list of {tool, arguments, why} objects", example=PLAN_EXAMPLES[0])
    if len(raw) > MAX_STEPS:
        return args, tool_error(f"A plan may propose at most {MAX_STEPS} steps; this one has {len(raw)}.", field="steps",
                                expected=f"at most {MAX_STEPS} steps, or several smaller plans")
    steps: list[dict[str, Any]] = []
    for i, s in enumerate(raw):
        if not isinstance(s, dict):
            return args, tool_error(f"Step {i + 1} is not an object.", field="steps", example=PLAN_EXAMPLES[0])
        tool = str(s.get("tool") or "").strip()
        a = s.get("arguments")
        if a is None:
            a = {}
        if not tool:
            return args, tool_error(f"Step {i + 1} has no 'tool'.", field="steps", example=PLAN_EXAMPLES[0])
        if not isinstance(a, dict):
            return args, tool_error(f"Step {i + 1} ({tool}): 'arguments' must be an object.", field="steps",
                                    expected="the arguments object you would pass to that tool", example=PLAN_EXAMPLES[0])
        if tool == PLAN_TOOL:
            return args, tool_error("A plan cannot contain propose_plan.", field="steps",
                                    expected="the actions themselves")
        if modes is not None:
            if tool not in modes:
                return args, tool_error(f"Step {i + 1}: there is no tool called {tool}.", field="steps",
                                        expected="names of tools listed in this request")
            if modes[tool] == "off":
                return args, tool_error(f"Step {i + 1}: {tool} is turned off for this chat, so it cannot be approved.",
                                        field="steps", alternative="leave that step out and tell the user it is off")
        steps.append({"tool": tool, "arguments": a, "why": str(s.get("why") or "")[:MAX_WHY]})
    return {"title": str(args.get("title") or "")[:MAX_TITLE], "steps": steps}, None


def parse_plan_edits(raw: Any) -> dict[int, dict[str, Any] | None] | None:
    """The approval route's optional `steps` payload: which proposed steps to authorise, and with what arguments.

    {idx: arguments-or-None}. A proposed step the user left out is dropped, not rejected: it simply loses its
    pre-authorisation and asks again if the model calls it.
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("steps must be a list of {idx, arguments} objects")
    out: dict[int, dict[str, Any] | None] = {}
    for s in raw:
        if not isinstance(s, dict) or not isinstance(s.get("idx"), int):
            raise ValueError("each edited step needs an integer 'idx'")
        a = s.get("arguments")
        if a is not None and not isinstance(a, dict):
            raise ValueError("an edited step's 'arguments' must be an object")
        out[int(s["idx"])] = a
    return out


class Plans:
    """action_plans + plan_steps. One plan per propose_plan call; its approval row decides it."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ---- reads ----
    @staticmethod
    def _step(r: Any) -> dict[str, Any]:
        d = row_to_dict(r, ("args",)) or {}
        d["edited"] = bool(d.get("edited"))
        return d

    def _steps(self, c: Any, plan_id: str) -> list[dict[str, Any]]:
        return [self._step(r) for r in c.execute("SELECT * FROM plan_steps WHERE plan_id=? ORDER BY idx", (plan_id,))]

    def _plan(self, c: Any, r: Any) -> dict[str, Any] | None:
        d = row_to_dict(r)
        if d is None:
            return None
        d["tainted"] = bool(d["tainted"])
        d["steps"] = self._steps(c, d["plan_id"])
        return d

    def get(self, plan_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return self._plan(c, c.execute("SELECT * FROM action_plans WHERE plan_id=?", (plan_id,)).fetchone())

    def by_call(self, call_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return self._plan(c, c.execute("SELECT * FROM action_plans WHERE call_id=?", (call_id,)).fetchone())

    def for_run(self, run_id: str) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM action_plans WHERE run_id=? ORDER BY created_at", (run_id,)).fetchall()
            return [p for p in (self._plan(c, r) for r in rows) if p]

    def any_in(self, conversation_id: str | None) -> bool:
        """Whether this chat has any plan at all: one query per run, so the gate can skip the lookups otherwise."""
        if not conversation_id:
            return False
        with self.db.tx() as c:
            return c.execute("SELECT 1 FROM action_plans WHERE conversation_id=? LIMIT 1", (conversation_id,)).fetchone() is not None

    # ---- writes ----
    def open(self, call_id: str, args: dict[str, Any], *, run_id: str | None = None, conversation_id: str | None = None,
             message_id: str | None = None, tainted: bool = False) -> dict[str, Any]:
        """Record a proposed plan (pending) before its card is shown. `args` must already be normalize_plan()ed."""
        existing = self.by_call(call_id)
        if existing:  # a replayed round: the card and its digests stay as first proposed
            return existing
        plan_id = new_id()
        t = now()
        with self.db.tx() as c:
            c.execute("INSERT INTO action_plans(plan_id, call_id, run_id, conversation_id, message_id, title, status, tainted, created_at) "
                      "VALUES(?,?,?,?,?,?,'pending',?,?)",
                      (plan_id, call_id, run_id, conversation_id, message_id, args.get("title") or "", int(tainted), t))
            for i, s in enumerate(args["steps"]):
                c.execute("INSERT INTO plan_steps(step_id, plan_id, idx, tool, args, args_digest, why, status) VALUES(?,?,?,?,?,?,?,'proposed')",
                          (new_id(), plan_id, i, s["tool"], json.dumps(s["arguments"], ensure_ascii=False, default=str),
                           args_digest(s["arguments"]), s.get("why") or ""))
        return self.get(plan_id) or {}

    def decide(self, call_id: str, decision: str, *, edits: dict[int, dict[str, Any] | None] | None = None,
               by: str = "user", note: str | None = None) -> dict[str, Any] | None:
        """Answer a pending plan. First decision wins, like the approval row it rides on.

        `edits` (from parse_plan_edits) is the user's change: the subset of proposed steps to authorise, each optionally
        with replacement arguments. An edited step's digest is re-derived from the edited values, so those -- and
        only those -- are what the step authorises. Steps left out are dropped, not rejected.
        """
        approved = decision != "deny"
        with self.db.tx() as c:
            r = c.execute("SELECT plan_id FROM action_plans WHERE call_id=?", (call_id,)).fetchone()
            if r is None:
                return None
            plan_id = r["plan_id"]
            n = c.execute("UPDATE action_plans SET status=?, decided_by=?, note=?, decided_at=? WHERE plan_id=? AND status='pending'",
                          ("approved" if approved else "rejected", by, note, now(), plan_id)).rowcount
            if not n:
                return self._plan(c, c.execute("SELECT * FROM action_plans WHERE plan_id=?", (plan_id,)).fetchone())
            if not approved:
                c.execute("UPDATE plan_steps SET status='rejected' WHERE plan_id=?", (plan_id,))
            else:
                for s in self._steps(c, plan_id):
                    if edits is not None and s["idx"] not in edits:
                        c.execute("UPDATE plan_steps SET status='dropped' WHERE step_id=?", (s["step_id"],))
                        continue
                    args = (edits or {}).get(s["idx"])
                    if args is None:
                        c.execute("UPDATE plan_steps SET status='approved' WHERE step_id=?", (s["step_id"],))
                        continue
                    digest = args_digest(args)
                    c.execute("UPDATE plan_steps SET status='approved', args=?, args_digest=?, edited=? WHERE step_id=?",
                              (json.dumps(args, ensure_ascii=False, default=str), digest,
                               int(digest != s["args_digest"]), s["step_id"]))
            return self._plan(c, c.execute("SELECT * FROM action_plans WHERE plan_id=?", (plan_id,)).fetchone())

    # ---- the gate ----
    def claim(self, run_id: str | None, tool: str, args: dict[str, Any], call_id: str) -> dict[str, Any] | None:
        """Consume one approved step matching this call's arguments exactly, or None.

        Single use, and scoped to the run the plan was approved in: the UPDATE's `status='approved'` guard is what
        makes the claim atomic, so two calls with the same digest cannot both ride one approval.
        """
        if not run_id:
            return None
        digest = args_digest(args)
        with self.db.tx() as c:
            r = c.execute("SELECT s.step_id FROM plan_steps s JOIN action_plans p ON p.plan_id=s.plan_id "
                          "WHERE p.run_id=? AND p.status='approved' AND s.status='approved' AND s.tool=? AND s.args_digest=? "
                          "ORDER BY s.idx LIMIT 1", (run_id, tool, digest)).fetchone()
            if r is None:
                return None
            if not c.execute("UPDATE plan_steps SET status='consumed', call_id=?, consumed_at=? WHERE step_id=? AND status='approved'",
                             (call_id, now(), r["step_id"])).rowcount:
                return None
            step = self._step(c.execute("SELECT * FROM plan_steps WHERE step_id=?", (r["step_id"],)).fetchone())
            p = c.execute("SELECT plan_id, title FROM action_plans WHERE plan_id=?", (step["plan_id"],)).fetchone()
        return {**step, "title": p["title"] if p else ""}

    def rejected(self, conversation_id: str | None, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
        """A step of a plan the user rejected, matched by digest: the model does not get to run it anyway.

        Chat-scoped and read-only, so a rejection keeps blocking after the run that carried it ended. Only the
        user's own no blocks (a stop that rejects an unanswered plan means "stop", not "never"). An approved step is
        looked for first, so re-proposing the same action and having it approved overrides an old no.
        """
        if not conversation_id:
            return None
        digest = args_digest(args)
        with self.db.tx() as c:
            r = c.execute("SELECT s.*, p.title FROM plan_steps s JOIN action_plans p ON p.plan_id=s.plan_id "
                          "WHERE p.conversation_id=? AND p.status='rejected' AND p.decided_by='user' "
                          "AND s.tool=? AND s.args_digest=? LIMIT 1",
                          (conversation_id, tool, digest)).fetchone()
        return self._step(r) if r is not None else None

    # ---- what the model is told ----
    @staticmethod
    def model_result(plan: dict[str, Any]) -> dict[str, Any]:
        """The propose_plan tool result: the decision, and for an approved plan the final arguments to use."""
        from .tools import tool_error  # local: see normalize_plan()

        if plan.get("status") == "rejected":
            e = tool_error(REJECTED_ERROR, alternative="ask the user what they would like instead")
            return {**e, "plan_id": plan["plan_id"], "status": "rejected", **({"user_note": plan["note"]} if plan.get("note") else {})}
        ok = [s for s in plan.get("steps") or [] if s["status"] in ("approved", "consumed")]
        dropped = [s for s in plan.get("steps") or [] if s["status"] == "dropped"]
        out: dict[str, Any] = {
            "plan_id": plan["plan_id"], "status": plan.get("status") or "pending", "approved": len(ok),
            "steps": [{"idx": s["idx"], "tool": s["tool"], "arguments": s["args"], "edited": s["edited"]} for s in ok],
            "note": APPROVED_NOTE,
        }
        if any(s["edited"] for s in ok):
            out["edits"] = EDITED_NOTE
        if dropped:
            out["dropped"] = [{"idx": s["idx"], "tool": s["tool"]} for s in dropped]
            out["dropped_note"] = DROPPED_NOTE
        if plan.get("note"):
            out["user_note"] = plan["note"]
        if not ok:
            out["note"] = "The user approved nothing from this plan. Do not run any of its steps; ask what they want instead."
        return out
