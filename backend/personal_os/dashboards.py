"""Custom dashboards: user-defined data sources (URL + secret), AI-generated widgets, AI summaries, daily recap."""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

import httpx

from . import llm
from .db import Database, new_id, now, row_to_dict
from .tools import UrlBlocked, guarded_request

SCHEMA = """
CREATE TABLE IF NOT EXISTS data_sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'http',          -- http | rss | internal
  config TEXT NOT NULL DEFAULT '{}',          -- {url, method, headers, auth_header, auth_prefix, query, body, internal}
  secret TEXT NOT NULL DEFAULT '',            -- API key; injected server-side, never sent to widgets
  description TEXT NOT NULL DEFAULT '',
  last_status TEXT,
  last_fetched_at REAL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS dashboards (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS widgets (
  id TEXT PRIMARY KEY,
  dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'html',          -- html (generated code) | summary (AI text) | markdown (static) | chart | stat | table (widget_spec.py)
  prompt TEXT NOT NULL DEFAULT '',
  source_ids TEXT NOT NULL DEFAULT '[]',
  code TEXT NOT NULL DEFAULT '',
  output TEXT NOT NULL DEFAULT '',
  refresh_minutes INTEGER NOT NULL DEFAULT 60,
  refreshed_at REAL,
  position INTEGER NOT NULL DEFAULT 0,
  width INTEGER NOT NULL DEFAULT 1,           -- 1..3 grid columns
  height INTEGER NOT NULL DEFAULT 280,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS recaps (
  day TEXT PRIMARY KEY,
  content TEXT NOT NULL,
  created_at REAL NOT NULL
);
"""

# Columns added after the table shipped (declarative widgets); CREATE TABLE IF NOT EXISTS will not add them.
_WIDGET_COLUMNS = {
    "spec": "TEXT NOT NULL DEFAULT '{}'",         # chart/stat/table binding (widget_spec.py)
    "data": "TEXT NOT NULL DEFAULT 'null'",       # cached bound rows {rows, stat}, refreshed on the widget's TTL
    "data_error": "TEXT NOT NULL DEFAULT ''",
}
_WIDGET_JSON = ("source_ids", "spec", "data")

INTERNAL_SOURCES = ["todos", "calendar", "gmail", "memories", "projects", "boards"]


class Dashboards:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)
            have = {r["name"] for r in c.execute("PRAGMA table_info(widgets)")}
            for col, ddl in _WIDGET_COLUMNS.items():
                if col not in have:
                    c.execute(f"ALTER TABLE widgets ADD COLUMN {col} {ddl}")

    # ---------- sources ----------
    def sources(self, with_secret: bool = False) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("SELECT * FROM data_sources ORDER BY created_at").fetchall()
        out = []
        for r in rows:
            d = row_to_dict(r, ("config",)) or {}
            d["has_secret"] = bool(d.get("secret"))
            if not with_secret:
                d.pop("secret", None)
            out.append(d)
        return out

    def source(self, id: str, with_secret: bool = False) -> dict[str, Any] | None:
        with self.db.tx() as c:
            d = row_to_dict(c.execute("SELECT * FROM data_sources WHERE id=?", (id,)).fetchone(), ("config",))
        if d:
            d["has_secret"] = bool(d.get("secret"))
            if not with_secret:
                d.pop("secret", None)
        return d

    def create_source(self, name: str, kind: str, config: dict[str, Any], secret: str = "", description: str = "") -> dict[str, Any]:
        sid = new_id()
        with self.db.tx() as c:
            c.execute("INSERT INTO data_sources(id,name,kind,config,secret,description,created_at) VALUES(?,?,?,?,?,?,?)",
                      (sid, name.strip(), kind, json.dumps(config), secret, description, now()))
        return self.source(sid)  # type: ignore[return-value]

    def update_source(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        with self.db.tx() as c:
            for k in ("name", "kind", "description"):
                if patch.get(k) is not None:
                    c.execute(f"UPDATE data_sources SET {k}=? WHERE id=?", (patch[k], id))
            if isinstance(patch.get("config"), dict):
                c.execute("UPDATE data_sources SET config=? WHERE id=?", (json.dumps(patch["config"]), id))
            if patch.get("secret") is not None and patch["secret"] != "":
                c.execute("UPDATE data_sources SET secret=? WHERE id=?", (patch["secret"], id))
            if patch.get("clear_secret"):
                c.execute("UPDATE data_sources SET secret='' WHERE id=?", (id,))
        return self.source(id)

    def delete_source(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM data_sources WHERE id=?", (id,))

    async def fetch_source(self, id: str, internal: dict[str, Any] | None = None) -> Any:
        """Fetch a source's data. Secrets are injected here so widgets never see them."""
        src = self.source(id, with_secret=True)
        if not src:
            raise ValueError("No such source")
        cfg = src["config"] or {}
        try:
            if src["kind"] == "internal":
                key = cfg.get("internal", "todos")
                data = (internal or {}).get(key)
                if data is None:
                    raise ValueError(f"Internal source '{key}' unavailable")
            elif src["kind"] == "rss":
                async with httpx.AsyncClient(timeout=20, follow_redirects=False) as c:
                    r = await guarded_request(c, "GET", cfg.get("url") or "")
                    r.raise_for_status()
                data = _parse_rss(r.text)
            else:
                headers = dict(cfg.get("headers") or {})
                params = dict(cfg.get("query") or {})
                if src["secret"]:
                    where = cfg.get("auth_in", "header")
                    if where == "query":
                        params[cfg.get("auth_param", "api_key")] = src["secret"]
                    else:
                        headers[cfg.get("auth_header", "Authorization")] = f"{cfg.get('auth_prefix', 'Bearer ')}{src['secret']}"
                async with httpx.AsyncClient(timeout=25, follow_redirects=False) as c:
                    r = await guarded_request(c, cfg.get("method", "GET"), cfg.get("url") or "", headers=headers,
                                             params=params, content=cfg.get("body") if cfg.get("body") else None)
                    r.raise_for_status()
                try:
                    data = r.json()
                except ValueError:
                    data = {"text": r.text[:200_000]}
            status = "ok"
        except Exception as e:  # noqa: BLE001
            status = f"error: {e}"
            with self.db.tx() as c:
                c.execute("UPDATE data_sources SET last_status=?, last_fetched_at=? WHERE id=?", (status, now(), id))
            raise
        with self.db.tx() as c:
            c.execute("UPDATE data_sources SET last_status=?, last_fetched_at=? WHERE id=?", (status, now(), id))
        return data

    # ---------- dashboards ----------
    def list(self) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute("SELECT d.*, (SELECT COUNT(*) FROM widgets WHERE dashboard_id=d.id) AS widget_count FROM dashboards d ORDER BY created_at").fetchall()
        return [row_to_dict(r) for r in rows]  # type: ignore[misc]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            d = row_to_dict(c.execute("SELECT * FROM dashboards WHERE id=?", (id,)).fetchone())
            if not d:
                return None
            ws = c.execute("SELECT * FROM widgets WHERE dashboard_id=? ORDER BY position, created_at", (id,)).fetchall()
        d["widgets"] = [row_to_dict(w, _WIDGET_JSON) for w in ws]
        return d

    def create(self, name: str, description: str = "") -> dict[str, Any]:
        did = new_id()
        with self.db.tx() as c:
            c.execute("INSERT INTO dashboards(id,name,description,created_at) VALUES(?,?,?,?)", (did, name.strip() or "Dashboard", description, now()))
        return self.get(did)  # type: ignore[return-value]

    def update(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        with self.db.tx() as c:
            for k in ("name", "description"):
                if patch.get(k) is not None:
                    c.execute(f"UPDATE dashboards SET {k}=? WHERE id=?", (patch[k], id))
        return self.get(id)

    def delete(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM dashboards WHERE id=?", (id,))

    # ---------- widgets ----------
    def widget(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM widgets WHERE id=?", (id,)).fetchone(), _WIDGET_JSON)

    def create_widget(self, dashboard_id: str, title: str, kind: str, prompt: str = "", source_ids: list[str] | None = None, code: str = "", output: str = "", width: int = 1, height: int = 280, refresh_minutes: int = 60, spec: dict[str, Any] | None = None) -> dict[str, Any]:
        wid = new_id()
        t = now()
        with self.db.tx() as c:
            pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM widgets WHERE dashboard_id=?", (dashboard_id,)).fetchone()[0]
            c.execute(
                "INSERT INTO widgets(id,dashboard_id,title,kind,prompt,source_ids,code,output,refresh_minutes,position,width,height,created_at,updated_at,spec) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (wid, dashboard_id, title.strip() or "Widget", kind, prompt, json.dumps(source_ids or []), code, output, refresh_minutes, pos, max(1, min(int(width), 3)), int(height), t, t, json.dumps(spec or {})),
            )
        return self.widget(wid)  # type: ignore[return-value]

    def update_widget(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        fields = {k: v for k, v in patch.items() if k in {"title", "kind", "prompt", "source_ids", "code", "output", "refresh_minutes", "position", "width", "height", "refreshed_at", "spec", "data", "data_error"} and v is not None}
        for k in ("source_ids", "spec", "data"):
            if k in fields:
                fields[k] = json.dumps(fields[k])
        if not fields:
            return self.widget(id)
        fields["updated_at"] = now()
        with self.db.tx() as c:
            c.execute(f"UPDATE widgets SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), id))
        return self.widget(id)

    def delete_widget(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM widgets WHERE id=?", (id,))

    # ---------- recaps ----------
    def get_recap(self, day: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM recaps WHERE day=?", (day,)).fetchone())

    def save_recap(self, day: str, content: str) -> dict[str, Any]:
        with self.db.tx() as c:
            c.execute("INSERT INTO recaps(day,content,created_at) VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET content=excluded.content, created_at=excluded.created_at", (day, content, now()))
        return {"day": day, "content": content, "created_at": now()}


def _parse_rss(xml: str) -> list[dict[str, str]]:
    import xml.etree.ElementTree as ET

    items = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return items
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        d: dict[str, str] = {}
        for ch in it:
            t = ch.tag.split("}")[-1]
            if t in ("title", "link", "description", "summary", "pubDate", "published", "updated", "content"):
                if t == "link" and ch.get("href"):
                    d["link"] = ch.get("href") or ""
                else:
                    d[t] = re.sub(r"<[^>]+>", " ", (ch.text or "")).strip()[:600]
        items.append(d)
        if len(items) >= 40:
            break
    return items


# ---------- LLM helpers ----------
WIDGET_SYSTEM = """You write small self-contained dashboard widgets as a single HTML document.
Rules:
- Output ONLY the HTML (starting with <!doctype html>), no markdown fences, no explanation.
- The widget runs in a sandboxed iframe with `allow-scripts` only. No external scripts or CSS; inline everything. No frameworks.
- Data: each source is available at the absolute URL given below via `fetch(url)`; responses are JSON (or {"text": ...}). Credentials are injected server-side; never ask for keys.
- Style: dark theme; body background #232220 (opaque — never transparent, the app composites badly with transparent documents); font-family system-ui; text color #ecebe8; muted #9c9a94; accent #d97757; compact 13px text; no page margins beyond 12px; content must fit the given size with internal scrolling if needed.
- Show a small "loading…" state, handle errors visibly, and render the requested information clearly (tables, lists, big numbers, simple inline SVG charts are all fine).
- Refresh data when the document loads. Optionally add a refresh button.
"""

SUMMARY_SYSTEM = "You write brief, useful summaries of live data for a personal dashboard. Use markdown: a short headline, then 3-8 bullets with the most decision-relevant facts (numbers, dates, names). Mention anomalies or things due soon first. No preamble."

RECAP_SYSTEM = """You write the user's daily recap for the home screen of their personal AI OS. Warm but efficient. Use markdown with short sections:
**Since yesterday** (what happened: chats, things learned, completed todos), **Today** (calendar, due todos, unread mail worth attention), **Suggested focus** (3 bullets max). Under 180 words. Skip empty sections. Never invent facts."""


def _line(text: Any, limit: int = 200) -> str:
    return " ".join(str(text or "").replace("\r", " ").split())[:limit]


def _fence(text: str) -> str:
    return "```\n" + str(text or "").replace("```", "'''") + "\n```"


class WidgetCodeRejected(ValueError):
    """The generated HTML still breaks the rules after its one repair; it is stored as an error, never rendered."""


_EXTERNAL = re.compile(r"""<script[^>]+\bsrc\s*=|<link[^>]+\bhref\s*=\s*["']?(?:https?:)?//|@import|url\(\s*["']?(?:https?:)?//|<iframe|<img[^>]+\bsrc\s*=\s*["']?(?:https?:)?//""", re.I)


def html_problems(code: str, secrets: list[str]) -> list[str]:
    out = []
    if not code.strip():
        out.append("the reply was empty")
    if _EXTERNAL.search(code):
        out.append("it loads an external script, stylesheet, image or frame; inline everything instead")
    if any(sec and sec in code for sec in secrets):
        out.append("it contains a source credential; credentials are injected server-side, never write them")
    return out


async def generate_widget_code(settings: dict[str, Any], model: str, prompt: str, sources: list[dict[str, Any]], base_url: str, width: int, height: int, samples: dict[str, Any],
                               secrets: list[str] | None = None) -> str:
    """Model call -> check -> at most ONE repair call (same shape as widget_spec.generate_spec). Raises WidgetCodeRejected."""
    src_lines = []
    for s in sources:
        sample = json.dumps(samples.get(s["id"]), ensure_ascii=False, default=str)
        if len(sample) > 1800:
            sample = sample[:1800] + "…"
        desc = _line(s.get("description"), 300) or "-"
        src_lines.append(f"- {_line(s.get('name'), 80)} ({_line(s.get('kind'), 40)}): fetch(\"{base_url}/sources/{s['id']}/fetch\")\n  description: {desc}\n  sample response: {sample}")
    user = f"Widget request:\n{_fence(prompt)}\n\nSize: about {width * 340}px wide × {height}px tall.\n\nData sources:\n" + ("\n".join(src_lines) if src_lines else "(none: build a static or self-computed widget)")
    msgs = [{"role": "system", "content": WIDGET_SYSTEM}, {"role": "user", "content": user}]
    raw = await llm.complete(settings, model, msgs)
    code = _wrap(raw)
    # Hard problems (an empty reply, external loads, a leaked credential) must be gone after the one repair or the
    # widget is rejected. Lint issues (no fetch(), a blank body) get the same repair but never block.
    problems, issues = html_problems(_unfence(raw), secrets or []), lint_widget_html(code, base_url, bool(sources))
    if problems or issues:  # one repair round, never more
        echo = "" if any("credential" in p for p in problems) else raw  # a credential is never echoed back
        try:
            raw2: str | None = await llm.complete(settings, model, msgs + [
                {"role": "assistant", "content": echo},
                {"role": "user", "content": "That widget has problems:\n- " + "\n- ".join(problems + issues)
                 + "\nReturn the complete corrected HTML document only."}])
        except Exception:  # noqa: BLE001 - a failed repair is judged as no repair
            raw2 = None
        if raw2 is not None and not html_problems(_unfence(raw2), secrets or []):
            fixed = _wrap(raw2)
            # Taken when it clears a hard problem or lints better; otherwise the original stands.
            if problems or len(lint_widget_html(fixed, base_url, bool(sources))) < len(issues):
                code, problems = fixed, []
    if problems:
        raise WidgetCodeRejected("; ".join(problems))
    return code


def _unfence(raw: str) -> str:
    return re.sub(r"^```(?:html)?\s*|\s*```$", "", raw.strip(), flags=re.I | re.M).strip()


def _wrap(code: str) -> str:
    code = _unfence(code)
    if "<html" not in code.lower():
        code = f"<!doctype html><html><body style='font-family:system-ui;color:#ecebe8;padding:12px'>{code}</body></html>"
    return code


def lint_widget_html(code: str, base_url: str = "", has_sources: bool = False) -> list[str]:
    """Problems the sandbox would turn into a blank or broken widget. Empty list = fine."""
    issues: list[str] = []
    remote = [u for u in re.findall(r"""(?:\bsrc\s*=|<link\b[^>]*\bhref\s*=|url\(|@import\s+)\s*["']?(https?://[^"'\s)>]+)""", code, flags=re.I)
              if not (base_url and u.startswith(base_url + "/sources/"))]
    if remote:
        issues.append(f"loads a remote asset ({remote[0]}); the sandbox blocks external scripts, styles and images, so inline everything")
    if has_sources and not re.search(r"\bfetch\s*\(", code):
        issues.append("never calls fetch() on any of the data sources")
    body = re.search(r"<body[^>]*>(.*?)(?:</body>|$)", code, flags=re.I | re.S)
    inner = body.group(1) if body else code
    if not re.search(r"<script\b", inner, flags=re.I) and not re.sub(r"<[^>]*>|\s", "", inner):
        issues.append("the body is empty")
    return issues


async def generate_summary(settings: dict[str, Any], model: str, prompt: str, data: dict[str, Any]) -> str:
    blob = json.dumps(data, ensure_ascii=False, default=str)
    if len(blob) > 24000:
        blob = blob[:24000] + "…(truncated)"
    focus = prompt.strip() if isinstance(prompt, str) and prompt.strip() else "what matters most"
    return await llm.complete(settings, model, [{"role": "system", "content": SUMMARY_SYSTEM},
                                                 {"role": "user", "content": f"Focus:\n{_fence(focus)}\n\nData (JSON):\n{blob}"}])


async def generate_recap(settings: dict[str, Any], model: str, facts: dict[str, Any]) -> str:
    blob = json.dumps(facts, ensure_ascii=False, default=str)
    if len(blob) > 20000:
        blob = blob[:20000] + "…"
    today = dt.date.today().strftime("%A, %B %d")
    return await llm.complete(settings, model, [{"role": "system", "content": RECAP_SYSTEM}, {"role": "user", "content": f"Today is {today}.\n\nFacts:\n{blob}"}])
