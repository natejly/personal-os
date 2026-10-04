"""Workflows: a repeatable multi-step job written down once, approved by hash, resumable.

A definition is data (JSON, or YAML when PyYAML imports), never code:

    {name, description, params: {k: {type, required, default}},
     steps: [{id, tool | agent | fan_out, args, needs, approval: 'required', when}], output}

Templates are `{{param}}`, `{{step_id.result}}` (with `.key` / `.0` paths into it) and, inside a fan-out's
agent, `{{item}}` and `{{index}}`. A tiny substitutor fills them; nothing is evaluated.

The rules the rest of the app relies on:
  - a run starts only after the user approves its `plan_digest` = sha256(canonical definition + params). The
    digest is recomputed from the run's own snapshot when it starts and when it resumes, and an approval
    is refused if the saved definition changed since the run was proposed. Editing a definition therefore
    invalidates every run that was waiting on it.
  - every step is a `workflow_steps` row. Tool steps go through the Toolbox exactly like a chat's calls:
    the tool's mode (off / ask / on), taint forcing and the idempotency journal all apply, and a step
    marked `approval: required` parks on a durable approval row first. Agent and fan-out steps spawn
    subagents (subagents.py) under the same caps as any other child.
  - a done step is never run again. Resume starts at the first step that is not done; a side-effecting
    call that began before a crash is not repeated (the journal reports its outcome as unknown).
  - results of agent steps are untrusted text: they taint the run, so external tools later in it ask.
  - a failed step stops everything that needs it; independent steps still finish.
  - nothing starts by itself: `workflow_run` from chat or from a scheduled job only records a run that
    waits for approval.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Any, Callable

from . import permrules
from .db import new_id, now

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
STEP_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PARAM_TYPES = ("string", "number", "integer", "boolean", "list", "object")
MAX_STEPS = 40
MAX_RESULT_CHARS = 400_000
RESERVED_IDS = frozenset({"item", "index", "params", "result"})
# Tools a step may not call: they would start more work on their own (nesting, scheduling, desks) or ask
# the user something mid-run. A workflow expresses delegation with `agent` and `fan_out` steps instead.
TOOL_BLOCK = frozenset({
    "workflow_run", "workflow_resume", "workflow_list", "command_run", "command_list", "propose_plan", "agent_spawn", "agent_wait",
    "agent_stop", "desk_start", "desk_ask", "desk_done", "desk_deliver", "schedule_task", "cancel_scheduled_task", "todo_write",
})

REF = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+|\[\d+\])*)\s*\}\}")
_PATH = re.compile(r"[A-Za-z0-9_]+")
_FALSY = {"", "0", "false", "no", "none", "null", "[]", "{}"}


class TemplateError(ValueError):
    pass


# ---- parsing ------------------------------------------------------------------------------------

def parse(text: str) -> dict[str, Any]:
    """JSON, or YAML when PyYAML is importable. Raises ValueError with a readable line."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("the definition is empty")
    try:
        d = json.loads(raw)
    except ValueError as je:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            raise ValueError(f"not valid JSON ({je}); YAML needs PyYAML, which is not installed") from None
        try:
            d = yaml.safe_load(raw)
        except Exception as ye:  # noqa: BLE001 - yaml raises its own hierarchy
            raise ValueError(f"not valid JSON or YAML: {str(ye).splitlines()[0] if str(ye) else ye}") from None
    if not isinstance(d, dict):
        raise ValueError("a workflow definition is an object with name, params and steps")
    return d


def canonical(v: Any) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(defn: dict[str, Any], params: dict[str, Any]) -> str:
    return hashlib.sha256(canonical({"definition": normalize(defn), "params": params}).encode("utf-8")).hexdigest()


def def_digest(defn: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(normalize(defn)).encode("utf-8")).hexdigest()


def _agent_spec(a: Any) -> dict[str, Any] | None:
    if isinstance(a, str):
        return {"role": a}
    return dict(a) if isinstance(a, dict) else None


def normalize(defn: dict[str, Any]) -> dict[str, Any]:
    """The canonical shape: every optional key present, so the digest does not depend on what was left out."""
    steps = []
    for s in defn.get("steps") or []:
        if not isinstance(s, dict):
            steps.append(s)
            continue
        n: dict[str, Any] = {"id": s.get("id"), "needs": list(s.get("needs") or []), "approval": s.get("approval") or None,
                             "when": s.get("when") if s.get("when") is not None else None}
        if "tool" in s:
            n["tool"], n["args"] = s["tool"], s.get("args") or {}
        if "agent" in s:
            a = _agent_spec(s["agent"]) or s["agent"]
            if isinstance(a, dict) and isinstance(s.get("args"), dict):
                a = {**s["args"], **a}  # `agent: researcher` + `args: {task}` is the same step as an agent object
            n["agent"] = a
        if "fan_out" in s:
            f = s["fan_out"]
            if isinstance(f, dict):
                f = {**f, "agent": _agent_spec(f.get("agent")) or f.get("agent"),
                     "max_parallel": f.get("max_parallel") if f.get("max_parallel") is not None else None}
            n["fan_out"] = f
        steps.append(n)
    params = {}
    for k, p in (defn.get("params") or {}).items():
        p = p if isinstance(p, dict) else {}
        params[k] = {"type": p.get("type") or "string", "required": bool(p.get("required")), "default": p.get("default")}
    return {"name": defn.get("name"), "description": str(defn.get("description") or ""), "params": params, "steps": steps,
            "output": defn.get("output")}


# ---- templates ----------------------------------------------------------------------------------

def _path(s: str) -> list[str]:
    return _PATH.findall(s)


def refs(v: Any) -> list[list[str]]:
    """Every template path inside a value, as a list of segments."""
    out: list[list[str]] = []
    if isinstance(v, str):
        out += [_path(m.group(1)) for m in REF.finditer(v)]
    elif isinstance(v, dict):
        for x in v.values():
            out += refs(x)
    elif isinstance(v, list):
        for x in v:
            out += refs(x)
    return out


def _dig(base: Any, rest: list[str], label: str) -> Any:
    cur = base
    for seg in rest:
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        elif isinstance(cur, (list, tuple)) and seg.isdigit() and int(seg) < len(cur):
            cur = cur[int(seg)]
        else:
            raise TemplateError(f"{{{{{label}}}}} has no {seg!r}")
    return cur


def _text(v: Any) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)


def render(v: Any, params: dict[str, Any], steps: dict[str, Any] | None = None, vars: dict[str, Any] | None = None,
           keep_steps: bool = False) -> Any:
    """Fill the templates in `v`. A string that is exactly one template keeps the value's type (a list stays a
    list). `keep_steps` leaves `{{step.result}}` references untouched, which is how the plan shown for approval is
    built before any step has run."""
    steps = steps or {}
    vars = vars or {}

    def one(m: re.Match[str]) -> Any:
        path = _path(m.group(1))
        root, rest = path[0], path[1:]
        label = m.group(1)
        if root in vars:
            return _dig(vars[root], rest, label)
        if root in params:
            return _dig(params[root], rest, label)
        if root in ("item", "index") and keep_steps:
            return m.group(0)  # per-item values exist only while a fan-out runs; the plan shows them as written
        if rest and rest[0] == "result":
            if root in steps:
                return _dig(steps[root], rest[1:], label)
            if keep_steps:
                return m.group(0)
            raise TemplateError(f"{{{{{label}}}}}: step {root!r} has no result yet")
        raise TemplateError(f"{{{{{label}}}}} is not a parameter or a step result")

    if isinstance(v, str):
        whole = REF.fullmatch(v.strip())
        if whole:
            return one(whole)
        return REF.sub(lambda m: _text(one(m)), v)
    if isinstance(v, dict):
        return {k: render(x, params, steps, vars, keep_steps) for k, x in v.items()}
    if isinstance(v, list):
        return [render(x, params, steps, vars, keep_steps) for x in v]
    return v


def truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in _FALSY
    return bool(v)


# ---- validation ---------------------------------------------------------------------------------

def validate(defn: Any, tools: set[str], role_ok: Callable[[str], bool] | None = None) -> list[str]:
    """Every problem with a definition, as readable lines; empty means it can be saved and run."""
    errs: list[str] = []
    if not isinstance(defn, dict):
        return ["a workflow definition is an object with name, params and steps"]
    name = defn.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        errs.append("name must be lowercase letters, digits, - or _ (max 40)")
    params = defn.get("params") or {}
    if not isinstance(params, dict):
        errs.append("params must be an object of {name: {type, required, default}}")
        params = {}
    for k, p in params.items():
        if not isinstance(k, str) or not STEP_RE.match(k) or k in RESERVED_IDS:
            errs.append(f"param name {k!r} must be lowercase letters, digits or _ (and not item, index, params, result)")
        if not isinstance(p, dict):
            errs.append(f"param {k!r} must be an object")
        elif p.get("type", "string") not in PARAM_TYPES:
            errs.append(f"param {k!r} has type {p.get('type')!r}; use one of {', '.join(PARAM_TYPES)}")
    steps = defn.get("steps")
    if not isinstance(steps, list) or not steps:
        return [*errs, "steps must be a non-empty list"]
    if len(steps) > MAX_STEPS:
        errs.append(f"at most {MAX_STEPS} steps")
    ids: list[str] = []
    for i, s in enumerate(steps):
        where = f"step {i + 1}"
        if not isinstance(s, dict):
            errs.append(f"{where} must be an object")
            continue
        sid = s.get("id")
        if not isinstance(sid, str) or not STEP_RE.match(sid) or sid in RESERVED_IDS:
            errs.append(f"{where}: id must be lowercase letters, digits or _ (and not item, index, params, result)")
            continue
        where = f"step {sid!r}"
        if sid in ids:
            errs.append(f"{where}: duplicate id")
        if sid in params:
            errs.append(f"{where}: the id is also a parameter name")
        kinds = [k for k in ("tool", "agent", "fan_out") if k in s]
        if len(kinds) != 1:
            errs.append(f"{where}: needs exactly one of tool, agent or fan_out")
            ids.append(sid)
            continue
        kind = kinds[0]
        if s.get("approval") not in (None, "required"):
            errs.append(f"{where}: approval can only be 'required'")
        if s.get("when") is not None and not isinstance(s["when"], (str, bool)):
            errs.append(f"{where}: when must be a template string")
        needs = s.get("needs") or []
        if not isinstance(needs, list) or not all(isinstance(n, str) for n in needs):
            errs.append(f"{where}: needs must be a list of step ids")
            needs = []
        for n in needs:
            if n == sid:
                errs.append(f"{where}: needs itself (cycle)")
            elif n in ids:
                continue
            elif any(isinstance(o, dict) and o.get("id") == n for o in steps):
                errs.append(f"{where}: needs {n!r}, which comes later (list a step after the steps it needs; this also rules out cycles)")
            else:
                errs.append(f"{where}: needs unknown step {n!r}")
        in_fan = kind == "fan_out"
        body: Any
        if kind == "tool":
            t = s.get("tool")
            if not isinstance(t, str) or t not in tools:
                errs.append(f"{where}: unknown tool {t!r}")
            elif t in TOOL_BLOCK:
                errs.append(f"{where}: {t} cannot be a workflow step (use an agent or fan_out step to delegate)")
            if not isinstance(s.get("args", {}), dict):
                errs.append(f"{where}: args must be an object")
            body = s.get("args") or {}
        elif kind == "agent":
            a = _agent_spec(s["agent"])
            if a is None:
                errs.append(f"{where}: agent must be an object {{role, task}} or a role name")
                a = {}
            elif isinstance(s.get("args"), dict):
                a = {**s["args"], **a}
            errs += _agent_errors(where, a, role_ok)
            body = a
        else:
            f = s["fan_out"]
            if not isinstance(f, dict):
                errs.append(f"{where}: fan_out must be an object {{over, max_parallel, agent}}")
                f = {}
            if not f.get("over"):
                errs.append(f"{where}: fan_out needs `over` (a template that renders to a list)")
            mp = f.get("max_parallel")
            if mp is not None and not (isinstance(mp, int) and not isinstance(mp, bool) and 1 <= mp <= 16):
                errs.append(f"{where}: max_parallel must be a whole number from 1 to 16")
            a = _agent_spec(f.get("agent"))
            if a is None:
                errs.append(f"{where}: fan_out needs an agent {{role, task}}")
                a = {}
            errs += _agent_errors(where, a, role_ok)
            body = {"over": f.get("over"), "agent": a}
        for path in refs([body, s.get("when"), s.get("needs")]):
            root = path[0]
            if root in ("item", "index"):
                if not in_fan:
                    errs.append(f"{where}: {{{{{root}}}}} only exists inside a fan_out agent")
                continue
            if root in params:
                continue
            if root in ids and len(path) > 1 and path[1] == "result":
                if root not in needs:
                    needs = [*needs, root]  # a reference is a dependency; nothing to report
                continue
            if root in ids:
                errs.append(f"{where}: write {{{{{root}.result}}}} to use a step's output")
            elif any(isinstance(o, dict) and o.get("id") == root for o in steps):
                errs.append(f"{where}: refers to the later step {root!r}")
            else:
                errs.append(f"{where}: {{{{{'.'.join(path)}}}}} is not a parameter or an earlier step")
        if in_fan and isinstance(s.get("fan_out"), dict):
            over_refs = refs(s["fan_out"].get("over"))
            if any(p[0] in ("item", "index") for p in over_refs):
                errs.append(f"{where}: `over` cannot use item or index")
        ids.append(sid)
    out = defn.get("output")
    if out is not None:
        for path in refs(out):
            if path[0] not in params and not (path[0] in ids and len(path) > 1 and path[1] == "result"):
                errs.append(f"output: {{{{{'.'.join(path)}}}}} is not a parameter or a step result")
    return errs


def _agent_errors(where: str, a: dict[str, Any], role_ok: Callable[[str], bool] | None) -> list[str]:
    errs = []
    if not isinstance(a.get("task"), str) or not a["task"].strip():
        errs.append(f"{where}: the agent needs a task (a string, which may use templates)")
    role = a.get("role") or "researcher"
    if not isinstance(role, str) or (role_ok is not None and not role_ok(role)):
        errs.append(f"{where}: unknown agent role {role!r}")
    if a.get("tools") is not None and not (isinstance(a["tools"], list) and all(isinstance(t, str) for t in a["tools"])):
        errs.append(f"{where}: agent tools must be a list of tool names")
    return errs


def deps(step: dict[str, Any], defn: dict[str, Any]) -> list[str]:
    """Step ids this one waits for: its `needs` plus every step whose result it reads."""
    ids = {s["id"] for s in defn["steps"]}
    body: Any = [step.get("args"), step.get("agent"), step.get("fan_out"), step.get("when")]
    out = list(step.get("needs") or [])
    for p in refs(body):
        if p[0] in ids and p[0] != step["id"] and p[0] not in out:
            out.append(p[0])
    return out


def resolve_params(defn: dict[str, Any], given: dict[str, Any] | None) -> dict[str, Any]:
    """Defaults applied, required ones checked, values coerced to their declared type. Raises ValueError."""
    given = dict(given or {})
    decl = defn.get("params") or {}
    unknown = [k for k in given if k not in decl]
    if unknown:
        raise ValueError(f"unknown parameter(s): {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for k, p in decl.items():
        p = p if isinstance(p, dict) else {}
        if k in given and given[k] is not None:
            v = given[k]
        elif p.get("default") is not None:
            v = p["default"]
        elif p.get("required"):
            raise ValueError(f"parameter {k!r} is required")
        else:
            v = None
        out[k] = _coerce(k, v, p.get("type") or "string")
    return out


def _coerce(k: str, v: Any, typ: str) -> Any:
    if v is None:
        return None
    try:
        if typ == "string":
            return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v)
        if typ == "integer":
            if isinstance(v, bool):
                raise ValueError
            return int(v)
        if typ == "number":
            if isinstance(v, bool):
                raise ValueError
            f = float(v)
            return int(f) if f.is_integer() and not isinstance(v, float) else f
        if typ == "boolean":
            if isinstance(v, bool):
                return v
            if str(v).lower() in ("true", "1", "yes"):
                return True
            if str(v).lower() in ("false", "0", "no"):
                return False
            raise ValueError
        if typ in ("list", "object"):
            val = json.loads(v) if isinstance(v, str) else v
            if not isinstance(val, list if typ == "list" else dict):
                raise ValueError
            return val
    except (ValueError, TypeError):
        raise ValueError(f"parameter {k!r} must be a {typ}") from None
    return v


def expand(defn: dict[str, Any], params: dict[str, Any]) -> list[dict[str, Any]]:
    """The step list the user approves: parameters filled in, step results still shown as {{step.result}}."""
    out = []
    for s in normalize(defn)["steps"]:
        kind = "tool" if "tool" in s else "agent" if "agent" in s else "fan_out"
        r = render({k: s[k] for k in ("tool", "args", "agent", "fan_out", "when") if k in s}, params, keep_steps=True)
        out.append({"id": s["id"], "kind": kind, "needs": deps(s, defn), "approval": s.get("approval"), **r})
    return out


class RunBudget:
    """What the subagents of one workflow run are charged to: tokens and cost, with a cost cap (0 = none)."""

    def __init__(self, cap: float = 0.0) -> None:
        self.cap = cap
        self.tokens, self.cost, self.paused = 0, 0.0, 0.0
        self.t0 = time.monotonic()

    def add(self, pt: int, ct: int, cost: float | None) -> None:
        self.tokens += pt + ct
        self.cost += cost or 0.0

    def exceeded(self) -> str | None:
        return "cost" if self.cap > 0 and self.cost >= self.cap else None


# ---- storage ------------------------------------------------------------------------------------

DONE = ("done", "skipped")


class Workflows:
    """Saved definitions and the run / step rows. Nothing here executes anything."""

    def __init__(self, db: Any, tools: Callable[[], set[str]], role_ok: Callable[[str], bool] | None = None) -> None:
        self.db, self.tools, self.role_ok = db, tools, role_ok

    def check(self, defn: Any) -> list[str]:
        return validate(defn, self.tools(), self.role_ok)

    # ---- definitions
    @staticmethod
    def _wf(r: Any) -> dict[str, Any]:
        d = dict(r)
        d["definition"] = json.loads(d["definition"])
        d["digest"] = def_digest(d["definition"])
        d["params"] = d["definition"].get("params") or {}
        return d

    def list(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            return [self._wf(r) for r in c.execute("SELECT * FROM workflows ORDER BY name").fetchall()]

    def get(self, key: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM workflows WHERE id=? OR name=?", (key, key)).fetchone()
        return self._wf(r) if r else None

    def save(self, text: str, wf_id: str | None = None) -> dict[str, Any]:
        defn = parse(text)
        errs = self.check(defn)
        if errs:
            raise ValueError("; ".join(errs[:6]) + (f"; and {len(errs) - 6} more" if len(errs) > 6 else ""))
        n = normalize(defn)
        t = now()
        with self.db.tx() as c:
            clash = c.execute("SELECT id FROM workflows WHERE name=?", (n["name"],)).fetchone()
            if clash and clash["id"] != wf_id:
                raise ValueError(f"a workflow named {n['name']!r} already exists")
            if wf_id and c.execute("SELECT 1 FROM workflows WHERE id=?", (wf_id,)).fetchone():
                c.execute("UPDATE workflows SET name=?, description=?, definition=?, text=?, updated_at=? WHERE id=?",
                          (n["name"], n["description"], json.dumps(n, ensure_ascii=False), text, t, wf_id))
                rid = wf_id
            else:
                rid = "wf_" + new_id()
                c.execute("INSERT INTO workflows(id, name, description, definition, text, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                          (rid, n["name"], n["description"], json.dumps(n, ensure_ascii=False), text, t, t))
        return self.get(rid) or {}

    def delete(self, wf_id: str) -> bool:
        with self.db.tx() as c:
            return c.execute("DELETE FROM workflows WHERE id=?", (wf_id,)).rowcount > 0

    # ---- runs
    def _run(self, c: Any, r: Any) -> dict[str, Any] | None:
        if r is None:
            return None
        d = dict(r)
        for k in ("definition", "params", "result"):
            d[k] = json.loads(d[k]) if d.get(k) else None
        d["steps"] = [self._step(s) for s in c.execute("SELECT * FROM workflow_steps WHERE run_id=? ORDER BY idx", (d["id"],)).fetchall()]
        d["plan"] = expand(d["definition"], d["params"] or {}) if d["definition"] else []
        return d

    @staticmethod
    def _step(r: Any) -> dict[str, Any]:
        d = dict(r)
        for k in ("result", "items"):
            d[k] = json.loads(d[k]) if d.get(k) else None
        return d

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return self._run(c, c.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone())

    def list_runs(self, workflow_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM workflow_runs" + (" WHERE workflow_id=?" if workflow_id else "") +
                             " ORDER BY created_at DESC LIMIT ?", ((workflow_id, limit) if workflow_id else (limit,))).fetchall()
            out = []
            for r in rows:
                d = self._run(c, r)
                if d:
                    d.pop("plan", None)
                    out.append(d)
            return out

    def create_run(self, wf: dict[str, Any], params: dict[str, Any] | None, *, source: str = "user", project_id: str | None = None,
                   conversation_id: str | None = None) -> dict[str, Any]:
        """Record a run that waits for approval. Raises ValueError for a bad parameter or an invalid definition."""
        defn = normalize(wf["definition"])
        errs = self.check(defn)
        if errs:
            raise ValueError("the workflow no longer validates: " + "; ".join(errs[:4]))
        p = resolve_params(defn, params)
        rid = "wr_" + new_id()
        t = now()
        with self.db.tx() as c:
            c.execute("INSERT INTO workflow_runs(id, workflow_id, name, definition, params, plan_digest, status, project_id, conversation_id, "
                      "source, created_at, updated_at) VALUES(?,?,?,?,?,?,'awaiting_approval',?,?,?,?,?)",
                      (rid, wf.get("id"), defn["name"], json.dumps(defn, ensure_ascii=False), json.dumps(p, ensure_ascii=False, default=str),
                       digest(defn, p), project_id, conversation_id, source, t, t))
            for i, s in enumerate(defn["steps"]):
                c.execute("INSERT INTO workflow_steps(run_id, step_id, idx, kind, status, idempotency_key) VALUES(?,?,?,?,'pending',?)",
                          (rid, s["id"], i, "tool" if "tool" in s else "agent" if "agent" in s else "fan_out", f"{rid}:{s['id']}"))
        return self.get_run(rid) or {}

    def set_run(self, run_id: str, **f: Any) -> None:
        allowed = {"status", "approved_digest", "approved_at", "error", "result", "started_at", "ended_at"}
        cols = {k: (json.dumps(v, ensure_ascii=False, default=str) if k == "result" and v is not None else v)
                for k, v in f.items() if k in allowed}
        if cols:
            with self.db.tx() as c:
                c.execute(f"UPDATE workflow_runs SET {', '.join(k + '=?' for k in cols)}, updated_at=? WHERE id=?", (*cols.values(), now(), run_id))

    def set_step(self, run_id: str, step_id: str, **f: Any) -> None:
        allowed = {"status", "result", "items", "error", "approval_call_id", "started_at", "ended_at", "attempts"}
        cols = {}
        for k, v in f.items():
            if k not in allowed:
                continue
            if k in ("result", "items") and v is not None:
                v = json.dumps(v, ensure_ascii=False, default=str)
                if len(v) > MAX_RESULT_CHARS:
                    v = json.dumps({"truncated": True, "text": v[:MAX_RESULT_CHARS]})
            cols[k] = v
        if cols:
            with self.db.tx() as c:
                c.execute(f"UPDATE workflow_steps SET {', '.join(k + '=?' for k in cols)} WHERE run_id=? AND step_id=?", (*cols.values(), run_id, step_id))

    def reset_for_resume(self, run_id: str) -> None:
        """Everything that is not done goes back to pending (fan-out items already finished are kept in `items`)."""
        with self.db.tx() as c:
            c.execute("UPDATE workflow_steps SET status='pending', error=NULL WHERE run_id=? AND status NOT IN ('done','skipped')", (run_id,))

    def recover(self) -> int:
        """At startup: a run left running or waiting died with the last process. It becomes `interrupted`; Resume continues it."""
        with self.db.tx() as c:
            n = c.execute("UPDATE workflow_runs SET status='interrupted', error='Interrupted: the backend stopped while this ran.', updated_at=? "
                          "WHERE status IN ('running','waiting_approval')", (now(),)).rowcount
            c.execute("UPDATE workflow_steps SET status='pending' WHERE status IN ('running','waiting_approval')")
        return n


# ---- execution ----------------------------------------------------------------------------------

class ApprovalError(ValueError):
    pass


class Engine:
    """Runs approved workflow runs, one asyncio task each."""

    def __init__(self, store: Workflows, toolbox: Any, subagents: Any, run_store: Any, settings_fn: Callable[[], dict[str, Any]],
                 projects: Any = None) -> None:
        self.store, self.toolbox, self.subagents, self.runs = store, toolbox, subagents, run_store
        self.settings, self.projects = settings_fn, projects
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.stops: dict[str, asyncio.Event] = {}
        self.seq: dict[str, int] = {}

    # ---- the approval boundary
    def approve(self, run_id: str, plan_digest: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise ApprovalError("No such run.")
        if run["status"] != "awaiting_approval":
            raise ApprovalError(f"This run is {run['status']}, not waiting for approval.")
        self._bind(run, plan_digest)
        self.store.set_run(run_id, approved_digest=plan_digest, approved_at=now())
        return self.start(run_id)

    def _bind(self, run: dict[str, Any], plan_digest: str) -> None:
        """The digest the user saw must be this run's, still be what its own snapshot hashes to, and (for a saved
        workflow) still be what the saved definition says. Any edit in between has changed one of them."""
        actual = digest(run["definition"], run["params"] or {})
        if plan_digest != run["plan_digest"] or actual != run["plan_digest"]:
            raise ApprovalError("That approval is for a different plan than this run holds; review the plan again.")
        if run.get("workflow_id"):
            wf = self.store.get(run["workflow_id"])
            if wf is None or wf["digest"] != def_digest(run["definition"]):
                self.store.set_run(run["id"], status="stale", error="The workflow was edited after this run was proposed.")
                raise ApprovalError("The workflow was edited after this run was proposed; propose it again to approve the new version.")

    def start(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise ApprovalError("No such run.")
        if not run.get("approved_digest") or run["approved_digest"] != run["plan_digest"]:
            raise ApprovalError("This run has not been approved.")
        if run_id in self.tasks and not self.tasks[run_id].done():
            raise ApprovalError("This run is already running.")
        if digest(run["definition"], run["params"] or {}) != run["plan_digest"]:
            raise ApprovalError("This run's definition no longer matches the plan that was approved.")
        self.store.reset_for_resume(run_id)
        self.store.set_run(run_id, status="running", error=None, started_at=run.get("started_at") or now(), ended_at=None)
        self._tape(run_id, create=run)
        self.stops[run_id] = asyncio.Event()
        self.tasks[run_id] = asyncio.create_task(self._drive(run_id), name=f"workflow:{run_id}")
        return self.store.get_run(run_id) or {}

    def resume(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise ApprovalError("No such run.")
        if run["status"] not in ("interrupted", "failed", "cancelled"):
            raise ApprovalError(f"This run is {run['status']}; only an interrupted, failed or cancelled run resumes.")
        self._bind(run, run["plan_digest"])  # the approval stands only while the definition does
        if not run.get("approved_digest"):
            raise ApprovalError("This run was never approved.")
        return self.start(run_id)

    def cancel(self, run_id: str) -> bool:
        ev = self.stops.get(run_id)
        t = self.tasks.get(run_id)
        if ev is None or t is None or t.done():
            run = self.store.get_run(run_id)
            if run and run["status"] in ("awaiting_approval", "interrupted", "failed"):
                self.store.set_run(run_id, status="cancelled", ended_at=now())
                return True
            return False
        ev.set()
        return True

    async def wait(self, run_id: str, timeout: float = 60.0) -> None:
        t = self.tasks.get(run_id)
        if t is not None:
            await asyncio.wait_for(asyncio.shield(t), timeout)

    # ---- the tape (agent_runs row + events), so approvals and subagents hang off a real run
    def _tape(self, run_id: str, create: dict[str, Any] | None = None) -> None:
        if self.runs is None:
            return
        try:
            if create is not None:
                if self.runs.get(run_id) is None:
                    self.runs.create(run_id, create.get("conversation_id"), "workflow",
                                     {"workflow": create["name"], "params": create["params"], "conversation_id": create.get("conversation_id")})
                else:
                    self.runs.update(run_id, status="running", error=None)
                self.seq[run_id] = self.runs.last_seq(run_id)
        except Exception:  # noqa: BLE001 - no tape: the run still goes
            log.warning("could not open the tape of workflow run %s", run_id, exc_info=True)

    def _emit(self, run_id: str, event: str, data: dict[str, Any]) -> None:
        if self.runs is None:
            return
        self.seq[run_id] = self.seq.get(run_id, 0) + 1
        try:
            self.runs.append(run_id, self.seq[run_id], event, data)
        except Exception:  # noqa: BLE001
            log.debug("workflow tape write failed", exc_info=True)

    def _ctx(self, run: dict[str, Any], stop: asyncio.Event) -> dict[str, Any]:
        cfg = self.settings()
        project = None
        if self.projects is not None and run.get("project_id"):
            try:
                project = self.projects.get(run["project_id"])
            except Exception:  # noqa: BLE001
                project = None
        modes = self.toolbox.effective(cfg.get("tools") or {}, (project or {}).get("tools"), None)
        try:
            cap = float(cfg.get("workflowMaxCost") or 0)
        except (TypeError, ValueError):
            cap = 0.0
        return {"project_id": run.get("project_id"), "conversation_id": run.get("conversation_id"), "message_id": None,
                "tainted": False, "taint_sources": [], "allowed_urls": set(), "settings": cfg, "modes": modes, "depth": 0,
                "agent_run_id": run["id"], "model": cfg.get("defaultModel"), "stop": stop, "budget": RunBudget(max(0.0, cap)),
                "workflow_run_id": run["id"], "proposal_only": False}

    # ---- the loop
    async def _drive(self, run_id: str) -> None:
        stop = self.stops[run_id]
        try:
            await self._loop(run_id, stop)
        except asyncio.CancelledError:
            self.store.set_run(run_id, status="interrupted", error="Cancelled.", ended_at=now())
            self._close_tape(run_id, "interrupted", "Cancelled.")
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("workflow run %s failed", run_id, exc_info=True)
            self.store.set_run(run_id, status="failed", error=f"{type(e).__name__}: {e}", ended_at=now())
            self._close_tape(run_id, "error", f"{type(e).__name__}: {e}")

    def _close_tape(self, run_id: str, status: str, error: str | None = None) -> None:
        if self.runs is not None:
            self.runs.update(run_id, status=status, error=error, ended_at=time.time(), last_seq=self.seq.get(run_id, 0))

    async def _loop(self, run_id: str, stop: asyncio.Event) -> None:
        run = self.store.get_run(run_id) or {}
        defn = run["definition"]
        params = run["params"] or {}
        ctx = self._ctx(run, stop)
        rows = {s["step_id"]: s for s in run["steps"]}
        results: dict[str, Any] = {}
        state: dict[str, str] = {}
        for s in defn["steps"]:
            r = rows[s["id"]]
            if r["status"] in DONE:
                results[s["id"]] = r["result"]
                state[s["id"]] = r["status"]
                if r["status"] == "done" and self._taints(s):
                    ctx["tainted"] = True
        for step in defn["steps"]:
            sid = step["id"]
            if sid in state:
                continue
            if stop.is_set():
                self.store.set_run(run_id, status="cancelled", error="Cancelled by the user.", ended_at=now())
                self._close_tape(run_id, "cancelled", "Cancelled by the user.")
                return
            bad = [d for d in deps(step, defn) if state.get(d) in ("failed", "blocked")]
            if bad:
                state[sid] = "blocked"
                self.store.set_step(run_id, sid, status="blocked", error=f"needs {', '.join(bad)}, which did not finish", ended_at=now())
                continue
            try:
                if step.get("when") is not None and not truthy(render(step["when"], params, results)):
                    state[sid] = "skipped"
                    self.store.set_step(run_id, sid, status="skipped", result=None, ended_at=now())
                    continue
            except TemplateError as e:
                state[sid] = "failed"
                self.store.set_step(run_id, sid, status="failed", error=f"when: {e}", ended_at=now())
                continue
            self.store.set_step(run_id, sid, status="running", started_at=now(), error=None)
            self._emit(run_id, "workflow_step", {"step": sid, "status": "running"})
            try:
                if step.get("approval") == "required":
                    if not await self._approve_step(run, ctx, step, params, results, stop):
                        raise _StepFailed("declined by the user")
                result = await self._run_step(run, ctx, step, params, results, stop)
            except _StepFailed as e:
                state[sid] = "failed"
                self.store.set_step(run_id, sid, status="failed", error=str(e), ended_at=now())
                self._emit(run_id, "workflow_step", {"step": sid, "status": "failed", "error": str(e)})
                continue
            except TemplateError as e:
                state[sid] = "failed"
                self.store.set_step(run_id, sid, status="failed", error=str(e), ended_at=now())
                continue
            results[sid] = result
            state[sid] = "done"
            self.store.set_step(run_id, sid, status="done", result=result, ended_at=now())
            self._emit(run_id, "workflow_step", {"step": sid, "status": "done"})
        failed = [k for k, v in state.items() if v in ("failed", "blocked")]
        if stop.is_set():
            self.store.set_run(run_id, status="cancelled", error="Cancelled by the user.", ended_at=now())
            self._close_tape(run_id, "cancelled", "Cancelled by the user.")
            return
        if failed:
            first = next(k for k, v in state.items() if v == "failed") if "failed" in state.values() else failed[0]
            msg = f"Step {first!r} failed" + (f"; {len([v for v in state.values() if v == 'blocked'])} step(s) did not run" if "blocked" in state.values() else "")
            self.store.set_run(run_id, status="failed", error=msg, ended_at=now())
            self._close_tape(run_id, "error", msg)
            return
        out = None
        try:
            out = render(defn["output"], params, results) if defn.get("output") is not None else None
        except TemplateError as e:
            self.store.set_run(run_id, status="failed", error=f"output: {e}", ended_at=now())
            self._close_tape(run_id, "error", str(e))
            return
        self.store.set_run(run_id, status="done", result=out, ended_at=now())
        self._close_tape(run_id, "done")

    def _taints(self, step: dict[str, Any]) -> bool:
        if "tool" in step:
            return self.toolbox.taints(step["tool"])
        return True  # an agent's report is untrusted text

    async def _run_step(self, run: dict[str, Any], ctx: dict[str, Any], step: dict[str, Any], params: dict[str, Any],
                        results: dict[str, Any], stop: asyncio.Event) -> Any:
        if "tool" in step:
            return await self._tool_step(run, ctx, step, render(step.get("args") or {}, params, results), stop)
        if "agent" in step:
            a = render(step["agent"], params, results)
            return await self._agent(ctx, a, stop)
        return await self._fan_out(run, ctx, step, params, results, stop)

    # ---- tool steps
    async def _tool_step(self, run: dict[str, Any], ctx: dict[str, Any], step: dict[str, Any], args: dict[str, Any],
                         stop: asyncio.Event) -> Any:
        name = step["tool"]
        spec = self.toolbox.specs.get(name)
        raw = ctx["modes"].get(name, "off")
        if spec is None or raw == "off" or not self.toolbox.available(name):
            raise _StepFailed(f"{name} is not available (the tool is off or not connected)")
        # The chat loop's gates, in its order: untrusted content and calls that may never run unasked force a card,
        # a write outside the granted folders asks, then the argument-pattern rules (deny and the hardline list
        # refuse, ask cards, allow lifts a plain ask). The plan's approval covers the step it named, never a
        # forced card: those are decided per call.
        mode = self.toolbox.gate(name, raw, ctx, args)
        fs_ask = self.toolbox.fs_needs_ask(name, args, ctx)
        if fs_ask and mode == "on":
            mode = "ask"
        forced = mode != raw or (mode == "ask" and self.toolbox.forces_ask(name, args, ctx))
        cfg = self.settings()
        roots = [r for r in (cfg.get("workspaceRoots") or []) if isinstance(r, str) and r]
        perm = permrules.resolve(name, args, mode, forced, rules=cfg.get("permissionRules"), roots=roots)
        if perm.refusal:
            raise _StepFailed(f"{name} was refused: {perm.refusal}")
        mode, forced = perm.mode, perm.forced
        uid = f"{run['id']}:{step['id']}"
        if mode == "ask" and (forced or step.get("approval") != "required"):
            if not await self._ask(run, ctx, uid + ":call", name, args, forced, spec.danger, stop):
                raise _StepFailed(f"{name} was declined by the user")
        ctx["_step_idx"] = [s["id"] for s in run["definition"]["steps"]].index(step["id"])

        async def go() -> Any:
            ctx["fs_outside_ok"] = fs_ask  # approved above, or inside a granted folder
            try:
                return await self.toolbox.call(name, args, ctx)
            finally:
                ctx["fs_outside_ok"] = False

        if spec.danger in ("writes", "external") and self.runs is not None:
            res, replayed = await self.runs.call_once(run["id"], ctx["_step_idx"], name, args, go, call_id=uid)
            if replayed and isinstance(res, dict) and spec.taints and not res.get("error"):
                ctx["tainted"] = True
        else:
            res = await go()
        if isinstance(res, dict) and res.get("error"):
            raise _StepFailed(str(res["error"])[:500])
        return res

    # ---- agent steps
    async def _spawn(self, ctx: dict[str, Any], a: dict[str, Any], stop: asyncio.Event) -> Any:
        sub = self.subagents
        if sub is None:
            raise _StepFailed("subagents are not available")
        args = {k: v for k, v in a.items() if k in ("task", "role", "tools", "model", "root")}
        if not isinstance(args.get("task"), str) or not args["task"].strip():
            raise _StepFailed("the agent's task rendered empty")
        args["role"] = args.get("role") or "researcher"
        while True:
            if stop.is_set():
                raise _StepFailed("cancelled")
            got = sub._start(ctx, args)
            if isinstance(got, dict):
                if got.get("state") == "not_started":  # the app-wide cap: wait for a slot instead of failing the step
                    await asyncio.sleep(0.05)
                    continue
                raise _StepFailed(str(got.get("error") or got)[:500])
            return got

    async def _agent(self, ctx: dict[str, Any], a: dict[str, Any], stop: asyncio.Event) -> str:
        ch = await self._spawn(ctx, a, stop)
        try:
            await self.subagents._await(ch, ctx)
        finally:
            if not ch.finished.is_set():
                self.subagents.stop_tree(ch.id)
        ctx["tainted"] = True
        if "workflow:agent" not in ctx["taint_sources"]:
            ctx["taint_sources"].append("workflow:agent")
        if ch.state == "error" or ch.exit_reason in ("interrupted", "stale"):
            raise _StepFailed(f"the {ch.role.name} subagent ended: {ch.exit_reason or ch.error}")
        return ch.text

    async def _fan_out(self, run: dict[str, Any], ctx: dict[str, Any], step: dict[str, Any], params: dict[str, Any],
                       results: dict[str, Any], stop: asyncio.Event) -> list[Any]:
        f = step["fan_out"]
        over = render(f["over"], params, results)
        if isinstance(over, str):
            try:
                over = json.loads(over)
            except ValueError:
                over = [ln.strip() for ln in over.splitlines() if ln.strip()]
        if isinstance(over, dict):
            over = list(over.values()) if len(over) == 1 and isinstance(next(iter(over.values())), list) else list(over.items())
        if not isinstance(over, list):
            raise _StepFailed("fan_out `over` did not render to a list")
        cap = int(self.settings().get("workflowMaxFanOut") or 50)
        if len(over) > cap:
            raise _StepFailed(f"fan_out over {len(over)} items; the limit is {cap} (setting workflowMaxFanOut)")
        conc = max(1, min(int(f.get("max_parallel") or 4), int(self.settings().get("subagentMaxConcurrent") or 4)))
        prior = (next((s for s in run["steps"] if s["step_id"] == step["id"]), {}).get("items")) or {}
        items: dict[str, Any] = {k: v for k, v in prior.items() if int(k) < len(over)}
        sem = asyncio.Semaphore(conc)
        failures: list[str] = []
        lock = asyncio.Lock()

        async def one(i: int, item: Any) -> None:
            async with sem:
                if stop.is_set():
                    return
                try:
                    a = render(f["agent"], params, results, vars={"item": item, "index": i})
                    text = await self._agent(ctx, a, stop)
                except (_StepFailed, TemplateError) as e:
                    failures.append(f"item {i}: {e}")
                    return
                async with lock:
                    items[str(i)] = text
                    self.store.set_step(run["id"], step["id"], items=items)

        await asyncio.gather(*(one(i, it) for i, it in enumerate(over) if str(i) not in items))
        if failures:
            raise _StepFailed(f"{len(failures)} of {len(over)} items failed ({failures[0]}); finished items are kept for resume")
        if stop.is_set() and len(items) < len(over):
            raise _StepFailed("cancelled")
        return [items[str(i)] for i in range(len(over))]

    # ---- approvals
    async def _approve_step(self, run: dict[str, Any], ctx: dict[str, Any], step: dict[str, Any], params: dict[str, Any],
                            results: dict[str, Any], stop: asyncio.Event) -> bool:
        shown = (render(step.get("args") or {}, params, results) if "tool" in step
                 else render(step.get("agent") or step.get("fan_out") or {}, params, results, keep_steps=True))
        return await self._ask(run, ctx, f"{run['id']}:{step['id']}:step", step.get("tool") or "workflow_step", shown, False,
                               "external" if "tool" in step and (self.toolbox.specs.get(step["tool"]) and
                                                                 self.toolbox.specs[step["tool"]].danger == "external") else "writes", stop,
                               step_id=step["id"])

    async def _ask(self, run: dict[str, Any], ctx: dict[str, Any], uid: str, tool: str, args: dict[str, Any], forced: bool,
                   danger: str, stop: asyncio.Event, step_id: str | None = None) -> bool:
        """A durable approval row; the step waits as long as it takes. 'Always' is honoured once only."""
        sid = step_id or uid.split(":")[1]
        attempt = uid
        if self.runs is not None:
            n = 0
            while (old := self.runs.approval(attempt)) and old["status"] != "pending":
                n += 1
                attempt = f"{uid}#{n}"  # a resumed run asks again; the old answer does not carry over
            self.runs.open_approval(attempt, run["id"], tool, args, conversation_id=run.get("conversation_id"), forced=forced, danger=danger)
            self.runs.update(run["id"], status="awaiting_approval")
        self.store.set_step(run["id"], sid, status="waiting_approval", approval_call_id=attempt)
        self.store.set_run(run["id"], status="waiting_approval")
        t0 = time.time()
        decision = "deny"
        try:
            while True:
                if stop.is_set():
                    if self.runs is not None:
                        self.runs.decide(attempt, "deny", by="stop")
                    break
                row = self.runs.approval(attempt) if self.runs is not None else None
                if row and row["status"] != "pending":
                    decision = row["decision"]
                    break
                await asyncio.sleep(0.05)
        finally:
            b = ctx.get("budget")
            if b is not None:
                b.paused += time.time() - t0
            self.store.set_step(run["id"], sid, status="running")
            self.store.set_run(run["id"], status="running")
            if self.runs is not None:
                self.runs.update(run["id"], status="running")
        return decision in ("allow", "always_chat", "always_global")


class _StepFailed(Exception):
    pass


# ---- tool registration --------------------------------------------------------------------------

GROUP = "workflows"


def register(tb: Any) -> None:
    """Add workflow_list / workflow_run / workflow_resume. They resolve `tb.workflows` and `tb.workflow_engine` at call time
    (app.py wires them after the toolbox exists)."""
    from .tools import ToolSpec, _obj, tool_error
    R = tb.specs.__setitem__

    def _wf() -> Workflows | None:
        return getattr(tb, "workflows", None)

    def _eng() -> Engine | None:
        return getattr(tb, "workflow_engine", None)

    async def workflow_list(ctx: dict[str, Any]) -> Any:
        wf = _wf()
        if wf is None:
            return tool_error("Workflows are not available.")
        return {"workflows": [{"name": w["name"], "description": w["description"],
                               "params": {k: {"type": p.get("type"), "required": p.get("required")} for k, p in w["params"].items()},
                               "steps": len(w["definition"]["steps"])} for w in wf.list()]}

    R("workflow_list", ToolSpec("workflow_list", "List the user's saved workflows (repeatable multi-step jobs) with their parameters.",
                                _obj({}, []), workflow_list, GROUP, "safe", examples=[{}]))

    async def workflow_run(ctx: dict[str, Any], name: str, params: dict[str, Any] | None = None) -> Any:
        wf, eng = _wf(), _eng()
        if wf is None or eng is None:
            return tool_error("Workflows are not available.")
        w = wf.get(name)
        if w is None:
            return tool_error(f"No workflow named {name!r}.", field="name", expected=", ".join(x["name"] for x in wf.list()) or "(none saved)")
        try:
            run = wf.create_run(w, params, source="job" if ctx.get("proposal_only") else "chat", project_id=ctx.get("project_id"),
                                conversation_id=ctx.get("conversation_id"))
        except ValueError as e:
            return tool_error(f"workflow_run: {e}", field="params")
        return {"run_id": run["id"], "status": "awaiting_approval", "plan_digest": run["plan_digest"], "plan": run["plan"],
                "note": "NOT started. This records the run with its expanded plan; the user approves it in Library -> Workflows, "
                        "and only then does it run. Tell them it is waiting there and what it will do."}

    R("workflow_run", ToolSpec(
        "workflow_run", "Propose running a saved workflow with the given parameters. It never starts the run: it records the run "
        "and its expanded step list for the user to approve, by hash, in Library -> Workflows.",
        _obj({"name": {"type": "string"}, "params": {"type": "object", "description": "Values for the workflow's parameters"}}, ["name"]),
        workflow_run, GROUP, "writes", examples=[{"name": "folder-digest", "params": {"folder": "~/Documents/notes"}}]))

    async def workflow_resume(ctx: dict[str, Any], run_id: str) -> Any:
        eng = _eng()
        if eng is None:
            return tool_error("Workflows are not available.")
        if ctx.get("proposal_only"):
            return tool_error("workflow_resume starts work, and this is an unattended background run: it can only propose. "
                              "Tell the user the run is waiting to be resumed in Library -> Workflows.")
        try:
            run = eng.resume(run_id)
        except ApprovalError as e:
            return tool_error(f"workflow_resume: {e}", field="run_id")
        return {"run_id": run["id"], "status": run["status"], "note": "Resumed from the first step that was not done."}

    R("workflow_resume", ToolSpec(
        "workflow_resume", "Resume an interrupted or failed workflow run that the user already approved. It continues from the first "
        "step that is not done and never repeats a finished one. Always asks first.",
        _obj({"run_id": {"type": "string"}}, ["run_id"]), workflow_resume, GROUP, "plan", examples=[{"run_id": "wr_abc123"}]))
