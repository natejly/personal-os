"""Declarative dashboard widgets: chart | stat | table bound to a data source by a small JSON spec.

The model writes the SPEC once (which rows, which fields, which transforms); the client renders it with trusted
components, and a refresh just re-fetches the source and re-applies the spec with no model call (a trusted-catalog rule: nothing the model writes is ever executed). A spec is checked
against the real rows, cheap problems are fixed algorithmically, and at most ONE repair round goes back to the model
with the problem list (VegaChat-style), after which whatever is left is reported in `data_error`.

Everything below the LLM section is pure and synchronous so test_widget_spec.py can drive it without a model.
"""
from __future__ import annotations

import copy
import json
import re
import time
from typing import Any, Awaitable, Callable

from . import llm

KINDS = ("chart", "stat", "table")
CHART_TYPES = ("bar", "line", "area", "pie", "scatter")
STAT_AGGS = ("last", "sum", "mean", "count", "min", "max")
GROUP_AGGS = ("sum", "mean", "count", "min", "max")
CMPS = ("==", "!=", ">", "<", ">=", "<=", "contains")
OPS = ("sort", "limit", "filter", "group")
MAX_ROWS = 500
MAX_SERIES = 8

Row = dict[str, Any]
FetchFn = Callable[[str], Awaitable[Any]]


def _now() -> float:  # indirection so the TTL test can move the clock
    return time.time()


# ---------- values ----------
def to_number(v: Any) -> float | None:
    """A number for numbers and for the numeric strings APIs love ('$1,234', '12%', ' 3.5 '). Dates stay strings."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v == v and v not in (float("inf"), float("-inf")) else None
    if isinstance(v, str):
        s = re.sub(r"[\s$€£¥,]", "", v.strip())
        if s.endswith("%"):
            s = s[:-1]
        if not s or not re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", s):
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _clean(n: float) -> int | float:
    return int(n) if n == int(n) and abs(n) < 1e15 else n


# ---------- path ----------
_TOKEN = re.compile(r"\.([A-Za-z0-9_\-]+)|\[(\*|\d+)\]|\['([^']*)'\]")


def _tokens(path: str) -> list[tuple[str, Any]]:
    p = (path or "$").strip()
    if not p.startswith("$"):
        p = "$." + p
    rest, out, pos = p[1:], [], 0
    while pos < len(rest):
        m = _TOKEN.match(rest, pos)
        if not m:
            raise ValueError(f"bad path near '{rest[pos:pos + 12]}'")
        if m.group(1) is not None:
            out.append(("key", m.group(1)))
        elif m.group(3) is not None:
            out.append(("key", m.group(3)))
        elif m.group(2) == "*":
            out.append(("all", None))
        else:
            out.append(("idx", int(m.group(2))))
        pos = m.end()
    return out


def resolve_path(data: Any, path: str) -> list[Row]:
    """A minimal JSONPath ($, .key, [n], [*]) down to a list of row dicts. Never evaluates anything."""
    nodes: list[Any] = [data]
    fanned = False
    for kind, arg in _tokens(path):
        nxt: list[Any] = []
        for n in nodes:
            if kind == "key":
                if isinstance(n, dict) and arg in n:
                    nxt.append(n[arg])
            elif kind == "idx":
                if isinstance(n, list) and arg < len(n):
                    nxt.append(n[arg])
            else:
                fanned = True
                nxt.extend(n if isinstance(n, list) else list(n.values()) if isinstance(n, dict) else [])
        nodes = nxt
    return _rows_from(nodes, fanned)


def _row(v: Any) -> Row:
    return v if isinstance(v, dict) else {"value": v}


def _rows_from(nodes: list[Any], fanned: bool) -> list[Row]:
    if not nodes:
        return []
    if len(nodes) == 1 and not fanned:
        n = nodes[0]
        if isinstance(n, list):
            return [_row(i) for i in n]
        if isinstance(n, dict):
            if n and all(isinstance(v, dict) for v in n.values()):
                return [{"_key": k, **v} for k, v in n.items()]
            return [n]
        return [{"value": n}]
    return [_row(n) for n in nodes]


# ---------- transforms ----------
def _sort_key(v: Any) -> tuple[int, Any]:
    n = to_number(v)
    if n is not None:
        return (0, n)
    return (1, "" if v is None else str(v).lower())


def _cmp(a: Any, op: str, b: Any) -> bool:
    if op == "contains":
        return str(b).lower() in str(a).lower() if a is not None else False
    na, nb = to_number(a), to_number(b)
    x, y = (na, nb) if na is not None and nb is not None else (str(a), str(b))
    return {"==": x == y, "!=": x != y, ">": x > y, "<": x < y, ">=": x >= y, "<=": x <= y}[op]  # type: ignore[operator]


def _agg(op: str, vals: list[Any]) -> Any:
    if op == "count":
        return len(vals)
    nums = [n for n in (to_number(v) for v in vals) if n is not None]
    if not nums:
        return None
    return _clean({"sum": sum(nums), "mean": sum(nums) / len(nums), "min": min(nums), "max": max(nums)}[op])


def apply_transforms(rows: list[Row], transforms: list[dict[str, Any]] | None) -> list[Row]:
    out = list(rows)
    for t in transforms or []:
        op = t.get("op") if isinstance(t, dict) else None
        if op == "sort":
            by = str(t.get("by", ""))
            desc = str(t.get("dir", "asc")).lower() == "desc"
            out.sort(key=lambda r: _sort_key(r.get(by)), reverse=desc)
        elif op == "limit":
            out = out[: max(0, min(int(t.get("n", MAX_ROWS)), MAX_ROWS))]
        elif op == "filter":
            cmp_ = t.get("cmp", "==")
            if cmp_ not in CMPS:
                raise ValueError(f"unknown filter comparison '{cmp_}'")
            f, val = str(t.get("field", "")), t.get("value")
            out = [r for r in out if _cmp(r.get(f), cmp_, val)]
        elif op == "group":
            by = str(t.get("by", ""))
            aggs = t.get("agg") or {}
            for field, a in aggs.items():
                if a not in GROUP_AGGS:
                    raise ValueError(f"unknown aggregate '{a}'")
            groups: dict[str, list[Row]] = {}
            for r in out:
                groups.setdefault(str(r.get(by)), []).append(r)
            out = [{by: k, **{f: _agg(a, [r.get(f) for r in rs]) if a != "count" else len(rs) for f, a in aggs.items()}}
                   for k, rs in groups.items()]
        else:
            raise ValueError(f"unknown transform op '{op}'")
    return out


# ---------- validate / autofix ----------
def _keys(rows: list[Row]) -> list[str]:
    seen: dict[str, None] = {}
    for r in rows:
        for k in r:
            seen.setdefault(k, None)
    return list(seen)


def _numeric_share(rows: list[Row], key: str) -> float:
    vals = [r.get(key) for r in rows if r.get(key) is not None]
    return sum(to_number(v) is not None for v in vals) / len(vals) if vals else 0.0


def _is_numeric_col(rows: list[Row], key: str) -> bool:
    return _numeric_share(rows, key) > 0.5


def _y_fields(spec: dict[str, Any]) -> list[str]:
    y = (spec.get("select") or {}).get("y") or []
    return [y] if isinstance(y, str) else [str(i) for i in y]


def validate_spec(spec: dict[str, Any], sample_rows: list[Row]) -> list[str]:
    """Problems with `spec` against the rows its path produced. Empty list = good."""
    problems: list[str] = []
    kind = spec.get("kind")
    if kind not in KINDS:
        return [f"kind must be one of {', '.join(KINDS)}"]
    if not sample_rows:
        return [f"path '{spec.get('path', '$')}' yields 0 rows"]
    try:
        rows = apply_transforms(sample_rows, spec.get("transforms"))
    except (ValueError, TypeError) as e:
        return [str(e)]
    if not rows:
        return ["the transforms leave no rows"]
    keys = _keys(rows)
    if kind == "chart":
        sel = spec.get("select") or {}
        x, ys = sel.get("x"), _y_fields(spec)
        if not x or x not in keys:
            problems.append(f"x field '{x}' is not in the rows (fields: {', '.join(keys)})")
        if not ys:
            problems.append("select.y lists no fields")
        if len(ys) > MAX_SERIES:
            problems.append(f"too many series ({len(ys)}; at most {MAX_SERIES})")
        for y in ys:
            if y not in keys:
                problems.append(f"y field '{y}' is not in the rows (fields: {', '.join(keys)})")
            elif not _is_numeric_col(rows, y):
                problems.append(f"y field '{y}' is not numeric in most rows")
        if (spec.get("chart") or {}).get("type") not in CHART_TYPES:
            problems.append(f"chart.type must be one of {', '.join(CHART_TYPES)}")
    elif kind == "stat":
        st = spec.get("stat") or {}
        agg, field = st.get("agg", "last"), st.get("field")
        if agg not in STAT_AGGS:
            problems.append(f"stat.agg must be one of {', '.join(STAT_AGGS)}")
        if agg != "count":
            if not field or field not in keys:
                problems.append(f"stat.field '{field}' is not in the rows (fields: {', '.join(keys)})")
            elif not _is_numeric_col(rows, field):
                problems.append(f"stat.field '{field}' is not numeric in most rows")
    else:
        for c in (spec.get("table") or {}).get("columns") or []:
            if not isinstance(c, dict) or c.get("key") not in keys:
                problems.append(f"table column '{c.get('key') if isinstance(c, dict) else c}' is not in the rows (fields: {', '.join(keys)})")
    return problems


def autofix(spec: dict[str, Any], rows: list[Row]) -> tuple[dict[str, Any], list[str]]:
    """The cheap repairs that need no model: drop absent fields, pick a default x and numeric ys. Returns (spec, remaining problems)."""
    spec = copy.deepcopy(spec)
    try:
        out = apply_transforms(rows, spec.get("transforms"))
    except (ValueError, TypeError):
        return spec, validate_spec(spec, rows)
    keys = _keys(out)
    kind = spec.get("kind")
    if kind == "chart" and out:
        sel = spec.setdefault("select", {})
        if sel.get("x") not in keys:
            sel["x"] = next((k for k in keys if not _is_numeric_col(out, k)), keys[0] if keys else None)
        ys = [y for y in _y_fields(spec) if y in keys and y != sel["x"]]
        if not ys:
            ys = [k for k in keys if k != sel["x"] and _is_numeric_col(out, k)]
        sel["y"] = ys[:MAX_SERIES]
        ch = spec.setdefault("chart", {})
        if ch.get("type") not in CHART_TYPES:
            ch["type"] = "bar"
    elif kind == "stat" and out:
        st = spec.setdefault("stat", {})
        st.setdefault("agg", "last")
        if st.get("agg") != "count" and st.get("field") not in keys:
            st["field"] = next((k for k in keys if _is_numeric_col(out, k)), st.get("field"))
    elif kind == "table":
        tb = spec.setdefault("table", {})
        tb["columns"] = [c for c in tb.get("columns") or [] if isinstance(c, dict) and c.get("key") in keys]
    return spec, validate_spec(spec, rows)


def coerce_rows(rows: list[Row], spec: dict[str, Any]) -> list[Row]:
    """Make the fields a chart or stat reads real numbers ('$1,234' -> 1234), only where most of a column already is."""
    fields = set(_y_fields(spec)) if spec.get("kind") == "chart" else set()
    if spec.get("kind") == "stat":
        fields |= {f for f in [(spec.get("stat") or {}).get("field"), (spec.get("stat") or {}).get("delta_field")] if f}
    fields = {f for f in fields if _is_numeric_col(rows, f)}
    if not fields:
        return rows
    return [{**r, **{f: (_clean(n) if (n := to_number(r.get(f))) is not None else r.get(f)) for f in fields if f in r}} for r in rows]


# ---------- stat ----------
def compute_stat(spec: dict[str, Any], rows: list[Row]) -> dict[str, Any] | None:
    st = spec.get("stat") or {}
    agg, field = st.get("agg", "last"), st.get("field")
    if agg not in STAT_AGGS or not rows:
        return None
    if agg == "count":
        value: Any = len(rows)
    else:
        nums = [n for n in (to_number(r.get(field)) for r in rows) if n is not None]
        if not nums:
            return None
        value = _clean({"last": nums[-1], "sum": sum(nums), "mean": sum(nums) / len(nums), "min": min(nums), "max": max(nums)}[agg])
    delta = None
    if st.get("delta_field"):
        delta = to_number(rows[-1].get(st["delta_field"]))
    elif agg == "last":
        nums = [n for n in (to_number(r.get(field)) for r in rows) if n is not None]
        delta = nums[-1] - nums[-2] if len(nums) >= 2 else None
    return {"value": value, "label": st.get("label") or field or "", "unit": st.get("unit") or "",
            "delta": _clean(delta) if delta is not None else None}


# ---------- binding and TTL ----------
async def bind(spec: dict[str, Any], fetch: FetchFn) -> dict[str, Any]:
    """Fetch the spec's source and shape it: {rows, stat, error}. Never raises; a failure is the `error` string."""
    try:
        data = await fetch(str(spec.get("source_id") or ""))
        rows = resolve_path(data, spec.get("path") or "$")
        rows = coerce_rows(rows, spec)
        rows = apply_transforms(rows, spec.get("transforms"))[:MAX_ROWS]
    except Exception as e:  # noqa: BLE001 - a dead source must show as an error on the widget, not a 500
        return {"rows": [], "stat": None, "error": str(e) or type(e).__name__}
    stat = compute_stat(spec, rows) if spec.get("kind") == "stat" else None
    err = "" if rows else "The source returned no rows for this widget's path."
    return {"rows": rows, "stat": stat, "error": err}


def is_fresh(widget: dict[str, Any], at: float | None = None) -> bool:
    """Cached rows are good for refresh_minutes after the last bind (per-widget cache TTL)."""
    ts = widget.get("refreshed_at")
    if ts is None or not widget.get("data"):
        return False
    return (at if at is not None else _now()) - float(ts) < max(1, int(widget.get("refresh_minutes") or 60)) * 60


# ---------- LLM ----------
SPEC_SYSTEM = """You turn a request into ONE JSON spec that binds a dashboard widget to a data source. Output ONLY the JSON object, no prose, no fences.
Shape:
{"kind": "chart|stat|table", "path": "$.items", "select": {"x": "<field>", "y": ["<numeric field>", ...]},
 "transforms": [{"op":"sort","by":"f","dir":"asc|desc"}, {"op":"limit","n":10}, {"op":"group","by":"f","agg":{"g":"sum|mean|count|min|max"}}, {"op":"filter","field":"f","cmp":"==|!=|>|<|>=|<=|contains","value":0}],
 "chart": {"type": "bar|line|area|pie|scatter", "stacked": false, "unit": "", "title": ""},
 "stat": {"field": "<numeric field>", "agg": "last|sum|mean|count|min|max", "label": "", "unit": "", "delta_field": "<optional>"},
 "table": {"columns": [{"key": "<field>", "label": ""}]}}
Rules:
- Include only the block for the chosen kind (chart -> select + chart; stat -> stat; table -> table). transforms is optional.
- `path` is a minimal JSONPath: $ then .key, [n], [*]. Choose one of the candidate paths given, or one beneath it. Field names must be copied exactly from the schema given.
- At most 8 y fields; y fields must be numeric. Order rows with a sort transform when the order matters (top-N = sort desc + limit).
"""


def _type_of(rows: list[Row], key: str) -> str:
    vals = [r.get(key) for r in rows if r.get(key) is not None]
    if not vals:
        return "null"
    if all(isinstance(v, bool) for v in vals):
        return "bool"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return "number"
    if _numeric_share(rows, key) > 0.5:
        return "numeric-string"
    if all(isinstance(v, str) for v in vals):
        return "string"
    return "mixed"


def describe_rows(rows: list[Row]) -> dict[str, Any]:
    """The true row schema, computed here from the fetched data rather than guessed from a truncated blob."""
    keys = _keys(rows)[:40]
    sample = [{k: (v if not isinstance(v, (dict, list)) else "<nested>") for k, v in list(r.items())[:40]} for r in rows[:5]]
    while sample and len(json.dumps(sample, default=str)) > 3000:  # a few wide rows must not crowd out the schema
        sample.pop()
    return {"rows": len(rows), "fields": {k: _type_of(rows, k) for k in keys}, "sample": sample}


def candidate_paths(data: Any, limit: int = 6, depth: int = 4) -> list[tuple[str, list[Row]]]:
    """Paths in `data` that yield row lists, shallowest first, so the model chooses from real options."""
    found: list[tuple[str, list[Row]]] = []

    def walk(node: Any, path: str, d: int) -> None:
        if len(found) >= limit:
            return
        if isinstance(node, list) and node and all(isinstance(i, dict) for i in node):
            found.append((path, node))
            return
        if isinstance(node, dict) and d < depth:
            if node and all(isinstance(v, dict) for v in node.values()) and len(node) > 1:
                found.append((path, resolve_path(data, path)))
            for k, v in node.items():
                if re.fullmatch(r"[A-Za-z0-9_\-]+", str(k)):
                    walk(v, f"{path}.{k}", d + 1)

    walk(data, "$", 0)
    if not found:
        rows = resolve_path(data, "$")
        if rows:
            found.append(("$", rows))
    return found[:limit]


def _parse_json(text: str) -> dict[str, Any]:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I | re.M).strip()
    m = re.search(r"\{.*\}", t, re.S)
    obj = json.loads(m.group(0) if m else t)
    if not isinstance(obj, dict):
        raise ValueError("the spec must be a JSON object")
    return obj


def _spec_prompt(prompt: str, source: dict[str, Any], data: Any, kind: str) -> str:
    cands = candidate_paths(data)
    blocks = [f"- path {p}: {json.dumps(describe_rows(r), default=str, ensure_ascii=False)}" for p, r in cands]
    return (f"Request: {prompt}\nWidget kind: {kind}\nSource: {source.get('name', '')} ({source.get('kind', '')}) "
            f"{source.get('description') or ''}\nCandidate row paths with their real schema and sample rows:\n"
            + ("\n".join(blocks) if blocks else "(the source returned no list of records; use path $)"))


async def generate_spec(settings: dict[str, Any], model: str, prompt: str, source: dict[str, Any], data: Any,
                        kind: str) -> tuple[dict[str, Any], list[str]]:
    """Model call -> validate -> algorithmic autofix -> at most ONE repair call. Returns (spec, remaining problems).

    `data` is the source's fetched JSON; the schema the model sees is computed from it here. Two model calls
    is the ceiling, so a flaky model cannot turn one widget into a loop.
    """
    user = _spec_prompt(prompt, source, data, kind)
    msgs: list[dict[str, str]] = [{"role": "system", "content": SPEC_SYSTEM}, {"role": "user", "content": user}]
    spec: dict[str, Any] = {}
    problems: list[str] = []
    for attempt in range(2):
        raw = await llm.complete(settings, model, msgs, kind="widget")
        try:
            spec = _parse_json(raw)
        except (ValueError, TypeError) as e:
            problems = [f"the reply was not valid JSON ({e})"]
        else:
            spec["kind"] = spec.get("kind") if spec.get("kind") in KINDS else kind
            spec["source_id"] = source.get("id") or spec.get("source_id") or ""
            spec.setdefault("path", "$")
            try:
                rows = resolve_path(data, spec["path"])
            except ValueError as e:
                rows, problems = [], [str(e)]
            else:
                spec, problems = autofix(spec, rows)
            if not problems:
                return spec, []
        if attempt == 0:
            msgs = msgs + [{"role": "assistant", "content": raw or ""},
                           {"role": "user", "content": "That spec has problems:\n- " + "\n- ".join(problems)
                            + "\nReturn the corrected JSON spec only."}]
    return spec, problems


# ---------- widget lifecycle (store = dashboards.Dashboards) ----------
async def _bind_and_store(store: Any, w: dict[str, Any], spec: dict[str, Any], fetch: FetchFn, error: str = "") -> dict[str, Any]:
    out = await bind(spec, fetch)
    return store.update_widget(w["id"], {"spec": spec, "data": {"rows": out["rows"], "stat": out["stat"]},
                                         "data_error": error or out["error"], "refreshed_at": _now()}) or w


async def run_widget(store: Any, w: dict[str, Any], settings: dict[str, Any], model: str, fetch: FetchFn,
                     regenerate: bool = False) -> dict[str, Any]:
    """Create/refresh a declarative widget. The model runs only to (re)generate the spec; a plain refresh is a re-bind."""
    sid = (w.get("source_ids") or [""])[0]
    if not sid or not store.source(sid):
        return store.update_widget(w["id"], {"data_error": "This widget needs a data source.", "refreshed_at": _now()}) or w
    spec = w.get("spec") or {}
    if regenerate or not spec:
        try:
            data = await fetch(sid)
        except Exception as e:  # noqa: BLE001
            return store.update_widget(w["id"], {"data_error": f"Could not fetch the source: {e}", "refreshed_at": _now()}) or w
        spec, problems = await generate_spec(settings, model, w.get("prompt") or w.get("title") or "", store.source(sid) or {},
                                             data, w["kind"])
        spec["kind"] = w["kind"]
        spec["source_id"] = sid
        once = {"cached": data}

        async def cached(_sid: str) -> Any:  # the data just fetched for the schema is what the first bind uses
            return once["cached"]
        return await _bind_and_store(store, w, spec, cached, "; ".join(problems))
    spec = {**spec, "source_id": sid}
    return await _bind_and_store(store, w, spec, fetch)


async def widget_data(store: Any, w: dict[str, Any], fetch: FetchFn) -> dict[str, Any]:
    """The cached rows when still inside the TTL, otherwise a re-bind (no model call). Returns the widget row."""
    if is_fresh(w):
        return w
    spec = w.get("spec") or {}
    if not spec:
        return w
    return await _bind_and_store(store, w, {**spec, "source_id": (w.get("source_ids") or [spec.get("source_id", "")])[0]}, fetch)
