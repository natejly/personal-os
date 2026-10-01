"""Action plans: the approval artifact, and the one decision function every tool call goes through.

An action plan is what the user approved, not what the agent intends. Approving step 3 approves
`sha256(canonical(args))` for that one call, once: `claim()` is a single-use atomic bind, so a second
use of the same step, one changed character, or a call the plan never contained all still raise a
card. Everything here is named `action_plans` / `ActionPlan*` so a future working-memory checklist can
land beside it as `chat_plans` / `Plan` without a rename.

`decide_call` is the other half: a pure function holding every rule that can stop a call, in one
order, so the contested region of `_chat_stream` has a testable centre and a second call site cannot
invent its own precedence. It is deliberately free of app state — it takes the gate and the repo as
arguments — which is what lets test_plans.py assert the ordering directly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Iterable

from .db import Database, new_id, now, row_to_dict
from .runlog import args_digest, canon

if TYPE_CHECKING:  # tools.py imports this module, so the ToolSpec import must not exist at runtime
    from .tools import ToolSpec

PLAN_TOOL = "propose_plan"
MAX_STEPS = 12
PLAN_STATUSES = ("pending", "approved", "rejected", "superseded")
STEP_STATUSES = ("proposed", "approved", "consumed", "done", "failed", "dropped", "rejected")
STEP_JSON = ("args",)
PLAN_JSON = ("expected_taint",)

# What stays callable while a plan is being drafted. `network` is in here on purpose: a plan whose
# arguments were invented without looking at anything is a plan whose exact-argument binding is worth
# very little, and expected taint (taint_expected below) removes the usual objection to reading first.
PLAN_SAFE_DANGER = ("safe", "network", "plan")
# Danger levels that change something, and so arm 'auto' plan mode.
MUTATING = ("writes", "executes", "external")
# A step the agent has already spent: its tool's taint was predicted by this plan by definition.
_CLAIMED = ("consumed", "done", "failed")

# A reason fragment, not a sentence: tools.denied() completes it as "<name> is <reason>.", so a
# subject or a full stop of its own would be doubled in the message the model actually reads.
PLAN_BLOCKED = ("not available while planning; put it in a plan step with these exact arguments "
                "and call propose_plan")
PROPOSE_ONLY = "this desk may only propose external actions"
STEP_REJECTED = "the user rejected this exact step"

# ---- propose_plan prose. The tool is registered in tools.py (it needs the Toolbox trampoline), but
# its wording is the contract this module enforces, so it is written beside the enforcement.
PLAN_TOOL_DESCRIPTION = (
    "Propose the ordered steps you intend to take, and stop. The user approves, edits or rejects the "
    "plan before anything runs. One step per real action, with the exact tool name and the exact "
    "arguments you will call it with. The arguments you write here are the arguments you will be held "
    "to: an approved step unlocks that one call with those arguments once, and anything else — a "
    "second use, one changed character, a tool the plan did not name — still asks the user. A step "
    "with no `tool` is a reasoning step that calls nothing. At most "
    f"{MAX_STEPS} steps; if the task needs no actions at all, do not propose a plan, just answer."
)
PLAN_TOOL_EXAMPLES: list[dict[str, Any]] = [
    {"title": "Add the dentist appointment",
     "steps": [{"title": "Create the calendar event", "tool": "calendar_create",
                "arguments": {"summary": "Dentist", "start": "2026-10-08T09:00", "end": "2026-10-08T10:00"},
                "why": "the user asked for Thursday morning"}]},
    {"title": "Reply to Dana about the invoice", "intent": "Find the thread, then answer it",
     "steps": [{"title": "Find Dana's invoice thread", "tool": "gmail_search",
                "arguments": {"query": "from:dana invoice"}, "why": "I need the thread before I can reply"},
               {"title": "Send the reply", "tool": "gmail_send",
                "arguments": {"to": "dana@example.com", "subject": "Re: Invoice 412",
                              "body": "Paid this morning - the reference is 412."},
                "why": "the user asked me to confirm the payment"}]},
    {"title": "Compare the competitor pricing and write it up",
     "intent": "Read their pricing page, decide what is comparable, leave a doc the user can keep",
     "steps": [{"title": "Read the pricing pages", "tool": "web_search",
                "arguments": {"query": "acme pricing per seat 2026"},
                "why": "the numbers have to come from their own site"},
               {"title": "Decide which tiers are comparable",
                "why": "no tool: this is the judgement the user actually wants"},
               {"title": "Write the comparison doc", "tool": "doc_create",
                "arguments": {"title": "Acme vs us - pricing", "content": "(written from the search results)"},
                "why": "the user asked for something they can keep and edit"}]},
]
PLAN_TOOL_ALTERNATIVE = "describe the steps in prose and ask the user how to proceed"
PLAN_STUB_ERROR = "propose_plan is answered by the approval gate"
# What the model is told once the user has decided.
PLAN_APPROVED_NOTE = ("Run these steps with exactly these arguments. Anything else still asks the user. "
                      "If reality differs from the plan, say so and propose a new one.")
PLAN_REJECTED_NOTE = ("The user rejected this plan. Stop: do not do the work anyway, and do not retry the "
                      "same steps. Say what you understood, then ask what they want instead or propose a "
                      "different plan.")

# These tables are owned here, not by db.py: Database._migrate runs inside Database.__init__, before
# ActionPlans(db) exists, so a _migrate entry for them would PRAGMA table_info an absent table.
# Post-release columns need an additive ALTER in __init__ below.
SCHEMA = """
CREATE TABLE IF NOT EXISTS action_plans (
  plan_id         TEXT PRIMARY KEY,
  call_id         TEXT UNIQUE,                      -- the approvals row this plan is decided through
  run_id          TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  desk_id         TEXT,
  message_id      TEXT,
  title           TEXT NOT NULL DEFAULT '',
  intent          TEXT NOT NULL DEFAULT '',
  status          TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|superseded
  tainted         INTEGER NOT NULL DEFAULT 0,       -- the reply was already tainted when proposed
  expected_taint  TEXT NOT NULL DEFAULT '[]',       -- JSON: tool names in this plan that taint
  note            TEXT NOT NULL DEFAULT '',
  decided_by      TEXT,                             -- user | stop | shutdown
  created_at      REAL NOT NULL,
  decided_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_action_plans_conv ON action_plans(conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_plans_desk ON action_plans(desk_id, created_at DESC);

CREATE TABLE IF NOT EXISTS plan_steps (
  step_id     TEXT PRIMARY KEY,
  plan_id     TEXT NOT NULL REFERENCES action_plans(plan_id) ON DELETE CASCADE,
  idx         INTEGER NOT NULL,
  title       TEXT NOT NULL DEFAULT '',
  tool        TEXT NOT NULL DEFAULT '',             -- '' = a reasoning step that calls nothing
  args        TEXT NOT NULL DEFAULT '{}',
  args_digest TEXT NOT NULL DEFAULT '',             -- runlog.args_digest(args); '' when tool is ''
  why         TEXT NOT NULL DEFAULT '',
  danger      TEXT NOT NULL DEFAULT 'safe',         -- snapshotted at propose time, for the card
  status      TEXT NOT NULL DEFAULT 'proposed',     -- proposed|approved|consumed|done|failed|dropped|rejected
  edited      INTEGER NOT NULL DEFAULT 0,
  call_id     TEXT,
  result_error TEXT,
  consumed_at REAL,
  UNIQUE(plan_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_plan_steps_claim ON plan_steps(plan_id, tool, args_digest, status);
"""

_DECISION_STATUS = {"approve": "approved", "edit": "approved", "reject": "rejected"}
_MISSING = object()


def _err(message: str, **extra: Any) -> dict[str, Any]:
    """tools.tool_error's shape, spelled out here because tools.py imports this module."""
    return {"error": message, **{k: v for k, v in extra.items() if v is not None}}


def normalize_plan(args: dict[str, Any], modes: dict[str, str], specs: dict[str, ToolSpec],
                   *, available: Callable[[str], bool] | None = None,
                   ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Validate BEFORE any card is shown, against the UNFILTERED mode map (what will be callable
    after approval), so the user is never asked to approve a plan that cannot execute.
    Caps at MAX_STEPS; every non-empty `tool` must exist in specs, be available(), and not be 'off'.
    Computes args_digest and copies ToolSpec.danger onto each step. Returns (steps, tool_error|None).

    `available` is Toolbox.available, passed in rather than reached for: `modes` carries every spec
    including the ones whose integration is not connected, so the mode map cannot answer this."""
    raw = args.get("steps")
    if not isinstance(raw, list) or not raw:
        return [], _err("propose_plan: `steps` must be a non-empty array of steps.", field="steps",
                        expected="[{title, tool?, arguments?, why}]", example=PLAN_TOOL_EXAMPLES[0],
                        try_instead="if the task needs no actions, answer in prose instead of proposing a plan")
    steps: list[dict[str, Any]] = []
    for i, s in enumerate(raw[:MAX_STEPS]):
        if not isinstance(s, dict):
            return [], _err(f"propose_plan: step {i + 1} is not an object.", field="steps",
                            expected="{title, tool?, arguments?, why}", example=PLAN_TOOL_EXAMPLES[0])
        tool = str(s.get("tool") or "").strip()
        sargs = s.get("arguments")
        if sargs is None:
            sargs = {}
        if not isinstance(sargs, dict):
            return [], _err(f"propose_plan: step {i + 1} ({tool or 'no tool'}): `arguments` must be an object.",
                            field="arguments", expected="the arguments you would pass to the tool",
                            example=PLAN_TOOL_EXAMPLES[0]["steps"][0])
        spec = specs.get(tool) if tool else None
        if tool:
            if spec is None:
                return [], _err(f"propose_plan: step {i + 1} names an unknown tool '{tool}'.", field="tool",
                                expected="a tool name from this request",
                                try_instead="plan a tool you were given, or leave `tool` out for a reasoning step")
            if available is not None and not available(tool):
                return [], _err(f"propose_plan: step {i + 1} uses '{tool}', which is not available right now.",
                                field="tool", try_instead="plan around it, or ask the user to connect it first")
            if modes.get(tool, "off") == "off":
                return [], _err(f"propose_plan: step {i + 1} uses '{tool}', which the user has turned off.",
                                field="tool", try_instead="plan a different tool, or ask the user to enable it")
        steps.append({
            "idx": i + 1,                      # 1-based: the card says "Step 2" and the edit payload agrees
            "title": str(s.get("title") or "").strip() or (tool or f"Step {i + 1}"),
            "tool": tool,
            "arguments": sargs,
            "args_digest": args_digest(sargs) if tool else "",
            "why": str(s.get("why") or "").strip(),
            "danger": spec.danger if spec else "safe",
            "taints": bool(spec and spec.taints),  # consumed by open() as expected_taint, not a column
        })
    return steps, None


def parse_plan_edits(raw: Any) -> dict[int, dict[str, Any] | None]:
    """[{idx, arguments}|{idx, drop:true}] -> {idx: args or None}. Raises ValueError on a bad shape."""
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise ValueError("steps must be an array of {idx, arguments} or {idx, drop: true}")
    out: dict[int, dict[str, Any] | None] = {}
    for e in raw:
        if not isinstance(e, dict):
            raise ValueError("each edit must be an object carrying an idx")
        idx = e.get("idx")
        if isinstance(idx, bool) or not isinstance(idx, int):
            raise ValueError("each edit needs an integer idx")
        if e.get("drop"):
            out[idx] = None
            continue
        a = e.get("arguments")
        if not isinstance(a, dict):
            raise ValueError(f"step {idx}: give `arguments` as an object, or `drop: true`")
        out[idx] = a
    return out


def _step(r: Any) -> dict[str, Any]:
    """A plan_steps row as the card and the model see it: the `args` column is `arguments` on the wire,
    matching ActionPlanStep in src/shared/types.ts and propose_plan's own step shape."""
    d = row_to_dict(r, STEP_JSON) or {}
    d["arguments"] = d.pop("args", None) or {}
    d["edited"] = bool(d.get("edited"))
    return d


def _plan(r: Any, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    d = row_to_dict(r, PLAN_JSON) or {}
    d["tainted"] = bool(d.get("tainted"))
    if not isinstance(d.get("expected_taint"), list):
        d["expected_taint"] = []
    if steps is not None:
        d["steps"] = steps
    return d


class ActionPlans:
    def __init__(self, db: Database) -> None:
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)

    # ---- writing ----
    def open(self, *, conversation_id: str, desk_id: str | None, run_id: str, message_id: str | None,
             call_id: str, title: str, intent: str, steps: list[dict[str, Any]], tainted: bool) -> dict[str, Any]:
        """Record a proposed plan. `steps` is normalize_plan's output, whose `taints` flags become the
        plan's expected_taint — the one thing that lets an approved send survive a planned web read."""
        pid = new_id()
        t = now()
        taint = sorted({s["tool"] for s in steps if s.get("taints") and s.get("tool")})
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO action_plans(plan_id,call_id,run_id,conversation_id,desk_id,message_id,title,intent,"
                "status,tainted,expected_taint,note,decided_by,created_at,decided_at)"
                " VALUES(?,?,?,?,?,?,?,?,'pending',?,?,'',NULL,?,NULL)",
                (pid, call_id, run_id, conversation_id, desk_id, message_id, title.strip(), intent.strip(),
                 1 if tainted else 0, json.dumps(taint), t))
            for i, s in enumerate(steps):
                c.execute(
                    "INSERT INTO plan_steps(step_id,plan_id,idx,title,tool,args,args_digest,why,danger,status,edited)"
                    " VALUES(?,?,?,?,?,?,?,?,?,'proposed',0)",
                    (new_id(), pid, s.get("idx", i + 1), s.get("title", ""), s.get("tool", ""),
                     json.dumps(s.get("arguments") or {}), s.get("args_digest", ""), s.get("why", ""),
                     s.get("danger", "safe")))
        got = self.get(pid)
        assert got is not None  # inserted one statement ago, in this process
        return got

    def supersede(self, conversation_id: str) -> None:
        """One live plan per conversation; called just before open(). A rejected plan keeps its status:
        `rejected()` reads its steps as a blocklist for the rest of the conversation."""
        with self.db.tx() as c:
            rows = c.execute("SELECT plan_id FROM action_plans WHERE conversation_id=?"
                             " AND status IN ('pending','approved')", (conversation_id,)).fetchall()
            c.execute("UPDATE action_plans SET status='superseded'"
                      " WHERE conversation_id=? AND status IN ('pending','approved')", (conversation_id,))
            # The steps go with the plan. claim() refuses them anyway, but a step left 'approved'
            # under a dead plan still reads as live to active(), remaining() and the checklist.
            for r in rows:
                c.execute("UPDATE plan_steps SET status='dropped' WHERE plan_id=?"
                          " AND status IN ('proposed','approved')", (r["plan_id"],))

    def decide(self, plan_id: str, decision: str, *, steps: dict[int, dict[str, Any] | None] | None = None,
               note: str = "", decided_by: str = "user") -> dict[str, Any] | None:
        """UPDATE ... WHERE plan_id=? AND status='pending' — rowcount is the lock. On approve: edited
        steps get a recomputed digest and edited=1; dropped indexes become 'dropped'."""
        status = _DECISION_STATUS.get(decision)
        if status is None:
            return None
        edits = steps or {}
        with self.db.tx() as c:
            cur = c.execute("UPDATE action_plans SET status=?, note=?, decided_by=?, decided_at=?"
                            " WHERE plan_id=? AND status='pending'",
                            (status, note.strip(), decided_by, now(), plan_id))
            if cur.rowcount != 1:  # already decided, superseded, or gone: the first decision wins
                return None
            rows = c.execute("SELECT * FROM plan_steps WHERE plan_id=? AND status='proposed' ORDER BY idx",
                             (plan_id,)).fetchall()
            live: list[str] = []
            for r in rows:
                if status == "rejected":
                    c.execute("UPDATE plan_steps SET status='rejected' WHERE step_id=?", (r["step_id"],))
                    continue
                edit = edits.get(r["idx"], _MISSING)
                if edit is None:
                    c.execute("UPDATE plan_steps SET status='dropped' WHERE step_id=?", (r["step_id"],))
                    continue
                if edit is _MISSING:
                    c.execute("UPDATE plan_steps SET status='approved' WHERE step_id=?", (r["step_id"],))
                else:
                    # Bound to the user's arguments, not the agent's: the digest is recomputed here, or
                    # claim() would go on matching the proposal whose details they just changed.
                    c.execute("UPDATE plan_steps SET status='approved', args=?, args_digest=?, edited=1"
                              " WHERE step_id=?",
                              (json.dumps(edit), args_digest(edit) if r["tool"] else "", r["step_id"]))
                if r["tool"]:
                    live.append(r["tool"])
            if status == "approved":
                # A dropped step's tool is no longer predicted taint. Leaving it listed would widen the
                # §4.6 exemption to cover a read the user has just taken out of the plan.
                old = json.loads(c.execute("SELECT expected_taint FROM action_plans WHERE plan_id=?",
                                           (plan_id,)).fetchone()[0] or "[]")
                c.execute("UPDATE action_plans SET expected_taint=? WHERE plan_id=?",
                          (json.dumps(sorted(set(old) & set(live))), plan_id))
        return self.get(plan_id)

    def claim(self, plan_id: str, tool: str, args: dict[str, Any], call_id: str) -> dict[str, Any] | None:
        """The single-use atomic bind: one approved step, one call, one set of arguments. The guard is
        the UPDATE's own rowcount, so two calls racing for the same step cannot both win.

        The OWNING PLAN must still be 'approved' too. Without that join a superseded plan's steps
        stay claimable, and a variant the user has just rejected is answered by the earlier plan
        that contained the same call — their "no" granted through a plan they already replaced."""
        digest = args_digest(args)
        with self.db.tx() as c:
            cur = c.execute(
                "UPDATE plan_steps SET status='consumed', call_id=?, consumed_at=? WHERE step_id = ("
                " SELECT s.step_id FROM plan_steps s JOIN action_plans p ON p.plan_id = s.plan_id"
                " WHERE s.plan_id=? AND p.status='approved' AND s.tool=? AND s.args_digest=?"
                " AND s.status='approved' ORDER BY s.idx LIMIT 1) AND status='approved'",
                (call_id, now(), plan_id, tool, digest))
            if cur.rowcount != 1:
                return None
            r = c.execute("SELECT * FROM plan_steps WHERE plan_id=? AND call_id=? AND status='consumed'"
                          " ORDER BY idx LIMIT 1", (plan_id, call_id)).fetchone()
        return _step(r) if r else None

    def finish(self, call_id: str, ok: bool, error: str | None = None) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE plan_steps SET status=?, result_error=? WHERE call_id=? AND status='consumed'",
                      ("done" if ok else "failed", None if ok else (error or "failed"), call_id))

    # ---- reading ----
    def get(self, plan_id: str, with_steps: bool = True) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM action_plans WHERE plan_id=?", (plan_id,)).fetchone()
            if not r:
                return None
            rows = c.execute("SELECT * FROM plan_steps WHERE plan_id=? ORDER BY idx",
                             (plan_id,)).fetchall() if with_steps else []
        return _plan(r, [_step(x) for x in rows] if with_steps else None)

    def by_call(self, call_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT plan_id FROM action_plans WHERE call_id=?", (call_id,)).fetchone()
        return self.get(r["plan_id"]) if r else None

    def for_desk(self, desk_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT plan_id FROM action_plans WHERE desk_id=? AND status!='superseded'"
                          " ORDER BY created_at DESC LIMIT 1", (desk_id,)).fetchone()
        return self.get(r["plan_id"]) if r else None

    def active(self, conversation_id: str) -> dict[str, Any] | None:
        """Newest 'approved' plan with at least one unconsumed step. None means plan mode still applies.

        The step has to be one claim() could actually consume. decide() promotes a tool-less
        reasoning step to 'approved' as well, and nothing ever spends it — so counting it would
        leave the plan live for the rest of the conversation, gating every unplanned call."""
        with self.db.tx() as c:
            r = c.execute(
                "SELECT p.plan_id FROM action_plans p WHERE p.conversation_id=? AND p.status='approved'"
                " AND EXISTS(SELECT 1 FROM plan_steps s WHERE s.plan_id=p.plan_id AND s.status='approved'"
                "            AND s.tool!='')"
                " ORDER BY p.created_at DESC LIMIT 1", (conversation_id,)).fetchone()
        return self.get(r["plan_id"]) if r else None

    def latest(self, conversation_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT plan_id FROM action_plans WHERE conversation_id=?"
                          " ORDER BY created_at DESC LIMIT 1", (conversation_id,)).fetchone()
        return self.get(r["plan_id"]) if r else None

    def rejected(self, conversation_id: str, tool: str, args: dict[str, Any]) -> bool:
        """Read-only blocklist, conversation-scoped, honouring only decided_by='user', so a Stop-induced
        rejection does not poison the rest of the conversation."""
        if not tool:
            return False
        with self.db.tx() as c:
            r = c.execute(
                "SELECT 1 FROM plan_steps s JOIN action_plans p ON p.plan_id=s.plan_id"
                " WHERE p.conversation_id=? AND p.decided_by='user' AND s.status='rejected'"
                " AND s.tool=? AND s.args_digest=? LIMIT 1",
                (conversation_id, tool, args_digest(args))).fetchone()
        return r is not None

    def remaining(self, plan_id: str) -> list[dict[str, Any]]:
        """Steps still to do: approved and unclaimed, or still waiting on the user."""
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM plan_steps WHERE plan_id=? AND status IN ('proposed','approved')"
                             " ORDER BY idx", (plan_id,)).fetchall()
        return [_step(r) for r in rows]

    def block(self, plan_id: str) -> str | None:
        """The approved plan as a `[x] / [>] / [ ]` checklist, re-injected as the LAST system message
        each round. convos.history() (repos.py:174-180) replays prose only, so without this the plan is
        invisible to the model from turn two onward and a chained desk turn redoes step one."""
        plan = self.get(plan_id)
        if not plan or plan["status"] != "approved":
            return None
        lines: list[str] = []
        for s in plan["steps"]:
            if s["status"] in ("dropped", "rejected"):
                continue
            mark = {"done": "[x]", "failed": "[x]", "consumed": "[>]"}.get(s["status"], "[ ]")
            line = f"{mark} {s['idx']}. {s['title']}"
            if s["status"] == "failed":
                line += f" — failed: {s['result_error'] or 'no result'}"
            elif s["tool"] and s["status"] in ("proposed", "approved"):
                # An unspent step carries its exact arguments: on a chained turn this block is the only
                # place the approved digest still exists, and a reworded argument claims nothing.
                line += f" — call {s['tool']} with {canon(s['arguments'])}"
            lines.append(line)
        if not lines:
            return None
        head = f"## Approved plan — {plan['title']}" if plan["title"] else "## Approved plan"
        return "\n".join([head, *lines, PLAN_APPROVED_NOTE])

    def model_result(self, plan: dict[str, Any]) -> dict[str, Any]:
        """What the propose_plan call itself answers with, once the user has decided."""
        if plan["status"] == "rejected":
            return {"status": "rejected", "note": plan.get("note") or "", "instruction": PLAN_REJECTED_NOTE}
        if plan["status"] != "approved":
            return {"status": plan["status"], "note": plan.get("note") or ""}
        all_steps = plan.get("steps") or []
        live = [s for s in all_steps if s["status"] != "dropped"]
        return {"status": "approved", "note": plan.get("note") or "",
                "edited": [s["idx"] for s in live if s["edited"]],
                "dropped": [s["idx"] for s in all_steps if s["status"] == "dropped"],
                "steps": [{"idx": s["idx"], "title": s["title"], "tool": s["tool"], "arguments": s["arguments"]}
                          for s in live],
                "instruction": PLAN_APPROVED_NOTE}


# ---- the one decision function every call site shares ----
@dataclass(frozen=True)
class CallPolicy:
    mode: str                        # on | ask | off
    forced: bool = False             # taint upgraded on -> ask, or blocked while planning
    claimed_step: str | None = None  # the plan_steps.step_id this call consumed
    off_plan: bool = False           # a mutating call the approved plan did not contain
    is_plan: bool = False
    deny: str | None = None          # a reason, when the call must not run at all


def taint_expected(plan: dict[str, Any] | None, sources: Iterable[str]) -> bool:
    """Is every taint source in this reply one the approved plan predicted?

    Either the tool is on the plan's expected_taint list — the card said so before the user approved —
    or it is a tool that actually claimed a step of this plan, which is the same promise kept. An
    off-plan fetch makes this False, and every external call goes back to asking."""
    srcs = [s for s in sources if s]
    if not srcs:
        return True
    if not plan:
        return False
    ok = set(plan.get("expected_taint") or [])
    ok.update(s["tool"] for s in plan.get("steps") or [] if s.get("tool") and s.get("status") in _CLAIMED)
    return all(s in ok for s in srcs)


def autoplan(name: str, *, specs: dict[str, ToolSpec], mode_pref: str, planning: bool,
             plan: dict[str, Any] | None = None) -> bool:
    """'auto' plan mode: the first mutating call of a reply arms planning instead of running. Pure,
    because the flip itself mutates the caller's in-memory conversation settings.

    An active plan suppresses it. After approval `planning` is False again while mode_pref is still
    'auto', so without this the next call re-arms planning and rule 3 denies the very call the user
    just authorised — in 'auto' an approved plan could never execute."""
    if planning or plan is not None or mode_pref != "auto" or name == PLAN_TOOL:
        return False
    spec = specs.get(name)
    return bool(spec and spec.danger in MUTATING)


def decide_call(name: str, args: dict[str, Any], *, raw_mode: str, specs: dict[str, ToolSpec],
                ctx: dict[str, Any], planning: bool, plan: dict[str, Any] | None, autonomy: str,
                gate: Callable[[str, str, dict[str, Any]], str], plans: ActionPlans,
                call_id: str | None = None) -> CallPolicy:
    """Effective policy for one call, in priority order. Every rule that can stop a call lives here.
    Note the ordering: the taint verdict is computed BEFORE claim() is attempted, because claim()
    consumes the step and a step burnt on a call the user then denies can never be reclaimed.

    `plan` is the approved plan WITH its steps (ActionPlans.active), or None. `call_id` is the uid the
    claim is recorded under; it falls back to ctx["call_id"] so the call site can set it on tool_ctx."""
    spec = specs.get(name)
    danger = spec.danger if spec else "safe"

    # 1. A plan is always a card: it can never resolve to `on`, and it can never buy a standing grant.
    if name == PLAN_TOOL:
        return CallPolicy("ask", is_plan=True)
    # 2. desk_ask is a question card, not a permission card.
    if name == "desk_ask":
        return CallPolicy("ask")
    # 3. Nothing consequential runs while a plan is being drafted. `forced` rides out on the tool_call
    #    event, so the UI can say "blocked while planning" rather than "turned off".
    if planning and danger not in PLAN_SAFE_DANGER:
        return CallPolicy("off", forced=True, deny=PLAN_BLOCKED)
    # 4. A propose-only desk may plan an external action, never perform one.
    if autonomy == "propose" and danger == "external":
        return CallPolicy("off", deny=PROPOSE_ONLY)

    would_force = gate(name, raw_mode, ctx) != raw_mode
    # 5. Taint this plan did not predict voids the pre-approval: ask, and consume nothing.
    if (danger == "external" and ctx.get("tainted") and raw_mode != "off"
            and not taint_expected(plan, ctx.get("taint_sources") or [])):
        return CallPolicy("ask", forced=would_force)

    # 6/7. The bind. A plan never overrides raw_mode 'off': normalize_plan refuses an off tool, so the
    # only way to arrive here is the user turning it off after approving, and they meant that.
    if plan and plan.get("status") == "approved" and raw_mode != "off":
        step = plans.claim(plan["plan_id"], name, args, call_id or ctx.get("call_id") or "")
        if step:
            return CallPolicy("on", claimed_step=step["step_id"])
        if danger != "safe":
            # Off plan asks, it is not denied: a plan is a strong default, not a cage.
            return CallPolicy("ask", off_plan=True)
    # 8. A step the user rejected stays rejected, with those exact arguments, for this conversation.
    if plans.rejected(str(ctx.get("conversation_id") or ""), name, args):
        return CallPolicy("off", deny=STEP_REJECTED)
    # 8b. 'Ask as it goes': a desk that does not plan first cards every change it makes instead,
    # one at a time. Below the bind, so a step this desk DID plan still runs on its approval;
    # forced, so the card cannot buy a standing grant that would quietly switch the mode off.
    if autonomy == "ask" and danger in MUTATING and raw_mode != "off":
        return CallPolicy("ask", forced=True)
    # 9. Otherwise today's behaviour, unchanged.
    mode = gate(name, raw_mode, ctx)
    return CallPolicy(mode, forced=mode != raw_mode)
