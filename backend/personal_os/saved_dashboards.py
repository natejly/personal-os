"""Saved dashboards: a dashboard is a normal doc in the "Dashboards" folder whose content is a JSON envelope
(docs/dashboards.md). Bindings hold references to data, never copies; a live dashboard re-reads them on every render and
falls back to the last good rows (dashboard_binding_cache, migration 38) when one fails; a snapshot dashboard carries
frozen rows in the file and never reads a binding."""
from __future__ import annotations

import html as htmllib
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .docs import Docs
from .health import Health
from .repos import is_isolated

FOLDER = "Dashboards"
FORMATS, MODES, KINDS, SOURCES = ("spec", "html"), ("live", "snapshot"), ("chart", "interactive"), ("doc", "health")
MAX_VIEWS = 12
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
_HOLE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)(?:\.([^\s{}]+))?\s*\}\}")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse(content: str) -> dict[str, Any] | None:
    """The envelope when `content` is one (by its marker), else None. Not validated: see validate."""
    if '"grainDashboard"' not in (content or ""):
        return None
    try:
        env = json.loads(content)
    except ValueError:
        return None
    return env if isinstance(env, dict) and env.get("grainDashboard") else None


def is_dashboard(content: str) -> bool:
    return parse(content) is not None


def validate(env: Any) -> dict[str, Any]:
    """The normalised envelope, or ValueError saying what is wrong. A live dashboard never keeps a snapshot."""
    if not isinstance(env, dict) or env.get("grainDashboard") != 1:
        raise ValueError('A dashboard is a JSON object with "grainDashboard": 1')
    fmt, mode = env.get("format") or "spec", env.get("mode") or "live"
    if fmt not in FORMATS:
        raise ValueError(f"format is one of {', '.join(FORMATS)}")
    if mode not in MODES:
        raise ValueError(f"mode is one of {', '.join(MODES)}")
    bindings, seen = [], set()
    for b in env.get("bindings") or []:
        name = b.get("name") if isinstance(b, dict) else None
        if not isinstance(name, str) or not _NAME.match(name) or name in seen:
            raise ValueError("Each binding needs a unique plain name (letters, digits, underscore)")
        seen.add(name)
        if b.get("source") == "doc":
            if not str(b.get("doc") or "").strip():
                raise ValueError(f"binding '{name}': a doc binding needs the doc's title or id")
            bindings.append({"name": name, "source": "doc", "doc": str(b["doc"]).strip()})
        elif b.get("source") == "health":
            if not str(b.get("metric") or "").strip():
                raise ValueError(f"binding '{name}': a health binding needs a metric")
            try:
                days = int(b.get("days") or 30)
            except (TypeError, ValueError):
                raise ValueError(f"binding '{name}': days is a number") from None
            hb = {"name": name, "source": "health", "metric": str(b["metric"]).strip(), "days": max(1, min(days, 3650))}
            if str(b.get("provider") or "").strip():
                hb["provider"] = str(b["provider"]).strip()
            bindings.append(hb)
        else:
            raise ValueError(f"binding '{name}': source is one of {', '.join(SOURCES)}")
    out: dict[str, Any] = {"grainDashboard": 1, "format": fmt, "mode": mode, "bindings": bindings}
    if fmt == "html":
        if not isinstance(env.get("html"), str) or not env["html"].strip():
            raise ValueError("An html dashboard needs a non-empty html string")
        out["html"] = env["html"]
    else:
        views = env.get("views")
        if not isinstance(views, list) or not 1 <= len(views) <= MAX_VIEWS:
            raise ValueError(f"A spec dashboard needs 1 to {MAX_VIEWS} views")
        out["views"] = []
        for i, v in enumerate(views):
            if not isinstance(v, dict) or v.get("kind") not in KINDS or not isinstance(v.get("spec"), dict):
                raise ValueError(f"views[{i}] needs kind ({' or '.join(KINDS)}) and a spec object")
            ref = v["spec"].get("data")
            if isinstance(ref, str) and (not ref.startswith("$") or ref[1:] not in seen):
                raise ValueError(f"views[{i}]: data '{ref}' must be $<binding name> of a declared binding")
            out["views"].append({"kind": v["kind"], "spec": v["spec"]})
    if mode == "snapshot":
        snap = env.get("snapshot")
        if not isinstance(snap, dict) or not isinstance(snap.get("data"), dict):
            raise ValueError("A snapshot dashboard needs snapshot.data")
        out["snapshot"] = {"savedAt": str(snap.get("savedAt") or ""), "data": snap["data"]}
    return out


def validate_text(content: str) -> dict[str, Any]:
    try:
        return validate(json.loads(content))
    except ValueError as e:  # JSONDecodeError is one
        raise ValueError(str(e)) from None


def _md_rows(text: str) -> list[dict[str, Any]]:
    """The first markdown table: one dict per row keyed by header; numeric-looking cells become numbers."""
    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    def num(s: str) -> Any:
        for t in (int, float):
            try:
                return t(s.replace(",", ""))
            except ValueError:
                pass
        return s
    lines = text.splitlines()
    for i in range(len(lines) - 1):
        if lines[i].lstrip().startswith("|") and re.fullmatch(r"[\s|:\-]+", lines[i + 1]) and "-" in lines[i + 1]:
            head, rows = cells(lines[i]), []
            for ln in lines[i + 2:]:
                if not ln.lstrip().startswith("|"):
                    break
                rows.append({h: num(v) for h, v in zip(head, cells(ln))})
            return rows
    return []


def _table(rows: list[Any]) -> str:
    cols = list(dict.fromkeys(k for r in rows if isinstance(r, dict) for k in r))
    if not cols:
        return ""
    e = lambda v: htmllib.escape(str(v if v is not None else ""))  # noqa: E731
    body = "".join("<tr>" + "".join(f"<td>{e(r.get(c))}</td>" for c in cols) + "</tr>" for r in rows if isinstance(r, dict))
    return "<table><thead><tr>" + "".join(f"<th>{e(c)}</th>" for c in cols) + f"</tr></thead><tbody>{body}</tbody></table>"


def fill_html(markup: str, data: dict[str, list[Any]]) -> str:
    def hole(m: re.Match[str]) -> str:
        rows = data.get(m.group(1)) or []
        if not m.group(2):
            return _table(rows)
        last = rows[-1] if rows and isinstance(rows[-1], dict) else {}
        v = last.get(m.group(2))
        return htmllib.escape(str(v)) if v is not None else ""
    return _HOLE.sub(hole, markup)


def label(b: dict[str, Any]) -> str:
    if b["source"] == "doc":
        return f"{b['doc']} doc"
    return f"Health: {b['metric']}" + (f" ({b['provider'].title()})" if b.get("provider") else "")


class SavedDashboards:
    def __init__(self, docs: Docs):
        self.docs, self.health = docs, Health(docs.db)

    # ---- binding sources ---------------------------------------------------------------------------
    def resolve(self, b: dict[str, Any], project_id: str | None = None) -> list[Any]:
        """One binding's rows now. Raises ValueError saying why it cannot."""
        if b["source"] == "health":
            m = self.health.resolve(b["metric"])
            if not m:
                raise ValueError(f"Unknown health metric '{b['metric']}'")
            since = (date.today() - timedelta(days=b["days"])).isoformat()
            prov = b.get("provider", "").lower()
            es = [e for e in self.health.entries(m["key"], since=since, limit=2000) if not prov or str(e["source"]).lower() == prov]
            return [{"date": e["day"], "value": e["value"]} for e in sorted(es, key=lambda e: e["day"])]
        d = self.docs.find(b["doc"])
        if not d or (is_isolated(self.docs.db, project_id) and d["project_id"] != project_id):
            raise ValueError(f"No doc '{b['doc']}'")
        try:
            data = json.loads(d["content"])
        except ValueError:
            data = None
        rows = data if isinstance(data, list) else data.get("rows") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            rows = _md_rows(d["content"])
        if not rows:
            raise ValueError(f"'{d['title']}' has no rows (a JSON array, a JSON object with rows, or a markdown table)")
        return rows

    def _cache_get(self, doc_id: str, name: str) -> tuple[list[Any], str] | None:
        with self.docs.db.tx() as c:
            r = c.execute("SELECT rows_json, fetched_at FROM dashboard_binding_cache WHERE doc_id=? AND name=?", (doc_id, name)).fetchone()
        return (json.loads(r["rows_json"]), r["fetched_at"]) if r else None

    def _cache_put(self, doc_id: str, name: str, rows: list[Any]) -> None:
        with self.docs.db.tx() as c:
            c.execute("INSERT OR REPLACE INTO dashboard_binding_cache(doc_id,name,rows_json,fetched_at) VALUES(?,?,?,?)",
                      (doc_id, name, json.dumps(rows), _iso()))

    # ---- reads ---------------------------------------------------------------------------------------
    def list(self, project_id: str | None = "__all__") -> list[dict[str, Any]]:
        sql = "SELECT id, project_id, title, content, updated_at FROM docs WHERE deleted_at IS NULL AND content LIKE '%grainDashboard%'"
        args: list[Any] = []
        if project_id != "__all__":
            sql += " AND project_id IS ?"
            args.append(project_id)
        with self.docs.db.tx() as c:
            rows = c.execute(sql + " ORDER BY updated_at DESC", args).fetchall()
        out = []
        for r in rows:
            if env := parse(r["content"]):
                out.append({"id": r["id"], "title": r["title"], "project_id": r["project_id"], "format": env.get("format") or "spec",
                            "mode": env.get("mode") or "live", "updated_at": r["updated_at"]})
        return out

    def find(self, name: str, visible: Any = None) -> dict[str, Any] | None:
        """A dashboard doc by id or case-insensitive title; `visible(row)` narrows the candidates."""
        key = (name or "").strip().lower()
        hit = next((r for r in self.list() if (visible is None or visible(r)) and key in (r["id"].lower(), (r["title"] or "").lower())), None)
        return self.docs.get(hit["id"]) if hit else None

    def render(self, d: dict[str, Any]) -> dict[str, Any]:
        env = validate_text(d["content"])
        snap = env["mode"] == "snapshot"
        data: dict[str, list[Any]] = {}
        failed: list[dict[str, Any]] = []
        if snap:
            data = {k: v for k, v in env["snapshot"]["data"].items() if isinstance(v, list)}
        else:
            for b in env["bindings"]:
                try:
                    data[b["name"]] = self.resolve(b, d["project_id"])
                    self._cache_put(d["id"], b["name"], data[b["name"]])
                except ValueError as e:
                    last = self._cache_get(d["id"], b["name"])
                    data[b["name"]] = last[0] if last else []
                    failed.append({"binding": b["name"], "error": str(e), "lastGoodAt": last[1] if last else None})
        views = None
        if env["format"] == "spec":
            views = [{"kind": v["kind"], "spec": {**v["spec"], "data": data.get(v["spec"]["data"][1:], [])}
                      if isinstance(v["spec"].get("data"), str) else v["spec"]} for v in env["views"]]
        return {"id": d["id"], "title": d["title"], "project_id": d["project_id"], "format": env["format"], "mode": env["mode"],
                "views": views, "html": fill_html(env["html"], data) if env["format"] == "html" else None,
                "data": data if env["format"] == "html" else None,
                "updatedAt": env["snapshot"]["savedAt"] if snap else _iso(), "failed": failed,
                "bindings": [{"name": b["name"], "source": b["source"], "label": label(b)} for b in env["bindings"]]}

    # ---- writes --------------------------------------------------------------------------------------
    def save(self, title: str, project_id: str | None = None, format: str | None = None, mode: str | None = None,
             bindings: list[Any] | None = None, views: list[Any] | None = None, html: str | None = None,
             author: str = "user") -> dict[str, Any]:
        env = {"grainDashboard": 1, "format": format or "spec", "mode": mode or "live", "bindings": bindings or [],
               "views": views, "html": html}
        if env["mode"] == "snapshot":  # resolve once, here; validate() below checks the shape of the bindings first
            checked = validate({**env, "mode": "live"})
            data = {}
            for b in checked["bindings"]:
                try:
                    data[b["name"]] = self.resolve(b, project_id)
                except ValueError as e:
                    raise ValueError(f"binding '{b['name']}': {e}") from None
            env["snapshot"] = {"savedAt": _iso(), "data": data}
        env = validate(env)
        self.docs.create_folder(FOLDER, project_id or "")
        return self.docs.create(title, json.dumps(env, indent=2), project_id, folder=FOLDER, author=author)

    def pin(self, doc_id: str, project_id: str | None) -> dict[str, Any] | None:
        self.docs.create_folder(FOLDER, project_id or "")
        return self.docs.move(doc_id, project_id, FOLDER)
