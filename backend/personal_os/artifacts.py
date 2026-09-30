"""Artifacts: AI-generated self-contained HTML documents the user keeps editing, with full version history.

The sibling of dashboards.py's widgets, with two differences: an artifact is a document the user owns
and revises (so every generation is kept as a version and can be restored), and it has no data sources,
so it never needs to reach the network at all.

RENDER POSTURE -- this module serves model-written code, so the render route's headers are the security
boundary, and they live here as RENDER_HEADERS rather than in the route so they cannot be forgotten.

Allowed, and why:
  * inline <script> and <style> (`script-src 'unsafe-inline'`, `style-src 'unsafe-inline'`) -- an artifact
    is one self-contained document by definition; there is no build step and no second file to load from,
    so inline code is the feature, not a concession. Nothing external may load: no host is allowlisted.
  * data:/blob: images, fonts and media, so a generated document can embed its own assets.
  * framing by the app itself (`frame-ancestors` = APP_FRAME_ANCESTORS: the packaged renderer at file:,
    the dev server, and this origin), so only Personal OS can put an artifact on screen.

Refused:
  * `connect-src 'none'` -- no fetch, no XMLHttpRequest, no WebSocket, no EventSource, no sendBeacon.
    An artifact cannot call this backend on the user's behalf and cannot post what it was given anywhere.
  * `default-src 'none'` with no host in any directive -- no remote script, style, image, font or frame,
    so nothing can be smuggled out through a URL either.
  * `form-action 'none'` and no allow-forms in the sandbox -- a submit cannot become a request.
  * `sandbox allow-scripts` on the response -- the document runs in an opaque origin even if the
    embedding iframe's own sandbox attribute is ever dropped, so it cannot reach the app's origin,
    open a window, navigate the top frame, or read cookies. Storage APIs (localStorage, sessionStorage,
    indexedDB) throw there; artifact state belongs in the artifact's own versions, not in the browser.
  * `base-uri 'none'`, `object-src 'none'`, `frame-src 'none'`, `worker-src 'none'`.

X-Frame-Options is deliberately absent: it cannot express "the packaged app at file:", and setting
SAMEORIGIN would break the very embedding frame-ancestors is there to permit.
"""
from __future__ import annotations

import re
from typing import Any

from . import llm
from .db import Database, new_id, now, row_to_dict

# These tables are owned here, not by db.py: Database._migrate runs inside Database.__init__, before
# Artifacts(db) exists, so a _migrate entry for them would see nothing. Post-release columns need an
# additive ALTER in __init__ below.
SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,  -- demote to personal scope, like notes and todos
  title TEXT NOT NULL DEFAULT '',           -- '' until the first version, then taken from the document's <title>
  kind TEXT NOT NULL DEFAULT 'html',        -- KINDS; html is the only one, the column is here for a later one
  prompt TEXT NOT NULL DEFAULT '',          -- the request behind the current version
  code TEXT NOT NULL DEFAULT '',            -- current document, denormalised so a render is one row read
  version INTEGER NOT NULL DEFAULT 0,       -- MAX(artifact_versions.version); 0 until the first save
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_updated ON artifacts(updated_at DESC);
CREATE TABLE IF NOT EXISTS artifact_versions (
  id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,                 -- 1-based, derived from artifacts.version so pruning cannot reuse a number
  code TEXT NOT NULL,
  prompt TEXT NOT NULL DEFAULT '',
  instruction TEXT NOT NULL DEFAULT '',     -- '' for the first version, the revise request afterwards
  source TEXT NOT NULL DEFAULT 'llm',       -- llm | user | restore
  created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_av_version ON artifact_versions(artifact_id, version);
"""

KINDS = ("html",)  # html only, so nothing the render route cannot serve under RENDER_HEADERS can be stored
VERSION_SOURCES = ("llm", "user", "restore")

MAX_CODE_CHARS = 400_000
MAX_REVISE_CHARS = 120_000  # a document past this cannot be round-tripped through a model, so we refuse rather than truncate it into garbage
MAX_VERSIONS = 50           # oldest versions are pruned; numbers still only ever go up

# Origins the renderer itself is served from: file: when packaged, the Vite dev server otherwise.
APP_FRAME_ANCESTORS = ("'self'", "file:", "http://localhost:5173", "http://127.0.0.1:5173")

ARTIFACT_CSP = "; ".join((
    "default-src 'none'",
    "script-src 'unsafe-inline'",
    "style-src 'unsafe-inline'",
    "img-src data: blob:",
    "font-src data:",
    "media-src data: blob:",
    "connect-src 'none'",
    "form-action 'none'",
    "base-uri 'none'",
    "object-src 'none'",
    "frame-src 'none'",
    "worker-src 'none'",
    "manifest-src 'none'",
    "frame-ancestors " + " ".join(APP_FRAME_ANCESTORS),
    "sandbox allow-scripts",
))

PERMISSIONS_POLICY = ", ".join(
    f"{f}=()" for f in (
        "accelerometer", "ambient-light-sensor", "autoplay", "camera", "clipboard-read", "clipboard-write",
        "display-capture", "encrypted-media", "geolocation", "gyroscope", "magnetometer", "microphone",
        "midi", "payment", "publickey-credentials-get", "screen-wake-lock", "serial", "usb", "xr-spatial-tracking",
    )
)

# The exact headers a render route must set. Pass render_headers() so a route cannot mutate this dict.
RENDER_HEADERS: dict[str, str] = {
    "Content-Security-Policy": ARTIFACT_CSP,
    "Content-Type": "text/html; charset=utf-8",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": PERMISSIONS_POLICY,
    "Cache-Control": "no-store",
}

EMPTY_HTML = "<!doctype html><html><body style='font-family:system-ui;color:#9c9a94;padding:12px'>Nothing generated yet.</body></html>"

# What the CSP will silently break, so the UI can say so before the user wonders why the artifact is blank.
BLOCKED_PATTERNS: tuple[tuple[str, str], ...] = (
    ("network", r"\bfetch\s*\("),
    ("network", r"\bXMLHttpRequest\b"),
    ("network", r"\bWebSocket\b"),
    ("network", r"\bEventSource\b"),
    ("network", r"\bsendBeacon\s*\("),
    ("network", r"\bimport\s*\("),
    ("remote-asset", r"<(?:script|link|iframe|object|embed)\b[^>]*\b(?:src|href)\s*=\s*['\"]?(?:https?:)?//"),
    ("storage", r"\b(?:localStorage|sessionStorage|indexedDB)\b"),
    ("form", r"<form\b"),
    ("popup", r"\bwindow\.open\s*\("),
)


def render_headers() -> dict[str, str]:
    """A fresh copy of RENDER_HEADERS, for a route to hand straight to its Response."""
    return dict(RENDER_HEADERS)


def blocked_capabilities(code: str) -> list[str]:
    """Which RENDER_HEADERS refusals this document would run into, deduped and stable-ordered."""
    out: list[str] = []
    for name, pattern in BLOCKED_PATTERNS:
        if name not in out and re.search(pattern, code, re.I):
            out.append(name)
    return out


class Artifacts:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)

    # ---------- artifacts ----------
    def list(self, project_id: str | None = "__all__", q: str = "") -> list[dict[str, Any]]:
        """Metadata only: `code` is left out so a long list stays cheap. Use get() to render one."""
        where, args = [], []
        if project_id != "__all__":
            if project_id is None:
                where.append("project_id IS NULL")
            else:
                where.append("project_id = ?")
                args.append(project_id)
        if q.strip():
            where.append("(title LIKE ? OR prompt LIKE ?)")
            args += [f"%{q}%", f"%{q}%"]
        sql = (
            "SELECT id,project_id,title,kind,prompt,version,created_at,updated_at,LENGTH(code) AS size,"
            "(SELECT COUNT(*) FROM artifact_versions WHERE artifact_id=artifacts.id) AS version_count "
            "FROM artifacts" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY updated_at DESC"
        )
        with self.db.tx() as c:
            return [row_to_dict(r) for r in c.execute(sql, args).fetchall()]  # type: ignore[misc]

    def get(self, id: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM artifacts WHERE id=?", (id,)).fetchone())

    def create(self, title: str = "", code: str = "", prompt: str = "", kind: str = "html",
               project_id: str | None = None, source: str = "llm") -> dict[str, Any]:
        """Create an artifact. Non-empty code is stored as version 1, so history starts at the beginning."""
        if kind not in KINDS:
            raise ValueError(f"Unknown artifact kind '{kind}'")
        code = _check_code(code)
        aid = new_id()
        t = now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO artifacts(id,project_id,title,kind,prompt,code,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (aid, project_id, title.strip()[:200], kind, prompt, "", 0, t, t),
            )
        if code:
            return self.save_version(aid, code, prompt=prompt, source=source)  # type: ignore[return-value]
        return self.get(aid)  # type: ignore[return-value]

    def update(self, id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        """Metadata only. Code changes go through save_version so nothing is ever overwritten in place."""
        fields = {k: v for k, v in patch.items() if k in {"title", "project_id", "prompt"} and v is not None}
        if patch.get("clear_project"):
            fields["project_id"] = None
        if not fields:
            return self.get(id)
        fields["updated_at"] = now()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.db.tx() as c:
            c.execute(f"UPDATE artifacts SET {sets} WHERE id=?", (*fields.values(), id))
        return self.get(id)

    def delete(self, id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM artifacts WHERE id=?", (id,))

    # ---------- versions ----------
    def save_version(self, id: str, code: str, prompt: str = "", instruction: str = "",
                     source: str = "llm") -> dict[str, Any] | None:
        """Append a version and point the artifact at it. The number comes from artifacts.version, which never decreases."""
        if source not in VERSION_SOURCES:
            raise ValueError(f"Unknown version source '{source}'")
        code = _check_code(code)
        if not code:
            raise ValueError("A version needs code")
        t = now()
        with self.db.tx() as c:
            row = c.execute("SELECT version,prompt,title FROM artifacts WHERE id=?", (id,)).fetchone()
            if not row:
                return None
            n = int(row["version"]) + 1
            c.execute(
                "INSERT INTO artifact_versions(id,artifact_id,version,code,prompt,instruction,source,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (new_id(), id, n, code, prompt or row["prompt"], instruction, source, t),
            )
            c.execute("UPDATE artifacts SET code=?, version=?, prompt=?, updated_at=? WHERE id=?",
                      (code, n, prompt or row["prompt"], t, id))
            if not (row["title"] or "").strip():
                c.execute("UPDATE artifacts SET title=? WHERE id=?", (_document_title(code), id))
            c.execute("DELETE FROM artifact_versions WHERE artifact_id=? AND version <= ?", (id, n - MAX_VERSIONS))
        return self.get(id)

    def versions(self, id: str) -> list[dict[str, Any]]:
        """Newest first, without code: a history list should not carry every document it describes."""
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT id,artifact_id,version,prompt,instruction,source,created_at,LENGTH(code) AS size "
                "FROM artifact_versions WHERE artifact_id=? ORDER BY version DESC", (id,)
            ).fetchall()
        return [row_to_dict(r) for r in rows]  # type: ignore[misc]

    def version(self, id: str, n: int) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM artifact_versions WHERE artifact_id=? AND version=?", (id, int(n))).fetchone())

    def restore(self, id: str, n: int) -> dict[str, Any] | None:
        """Undo, as a step forward: version n's code becomes a new version, so the history stays append-only."""
        old = self.version(id, n)
        if not old:
            return None
        return self.save_version(id, old["code"], prompt=old["prompt"], instruction=f"Restored version {n}", source="restore")


def _check_code(code: str) -> str:
    code = (code or "").strip()
    if len(code) > MAX_CODE_CHARS:
        raise ValueError(f"Artifact is {len(code)} characters, over the {MAX_CODE_CHARS} limit")
    return code


def _document_title(code: str) -> str:
    """The document's own <title>, which the model is told to set; used when the user gave no name."""
    m = re.search(r"<title[^>]*>(.*?)</title>", code, re.I | re.S)
    found = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    return found[:200] or "Artifact"


# ---------- LLM helpers ----------
ARTIFACT_SYSTEM = """You write a complete, self-contained artifact as a single HTML document: a tool, a visualisation, a game, a calculator, a page - whatever was asked for.
Rules:
- Output ONLY the HTML (starting with <!doctype html>), no markdown fences, no explanation.
- Set a short, specific <title> (2-4 words). It becomes the artifact's name.
- One file. Inline all CSS and JS. No external scripts, stylesheets, fonts or images: the document is served under a Content-Security-Policy with `default-src 'none'` and nothing remote will load. Embed small assets as data: URIs or inline SVG.
- No network of any kind. fetch, XMLHttpRequest, WebSocket, EventSource and sendBeacon are all blocked by `connect-src 'none'`. Compute or hard-code the data instead.
- No localStorage, sessionStorage or indexedDB: the document runs in an opaque origin and those APIs throw. Keep state in memory.
- No forms that submit and no window.open; they are blocked too. Use buttons and event handlers.
- Style: dark theme; background #262624; font-family system-ui; text #ecebe8; muted #9c9a94; accent #d97757; readable at any window size, responsive down to 360px wide, scroll internally rather than clipping.
- Make it work on load, handle empty and error states visibly, and keep it fast: no frameworks, no polyfills.
"""

REVISE_SYSTEM = """You revise an existing self-contained HTML artifact.
Rules:
- Output ONLY the complete revised HTML document, no markdown fences, no explanation, no diff.
- Change what the instruction asks for and leave everything else as it is, including the <title> unless the instruction is about the name.
- The same restrictions still apply: one file, everything inline, no external resources, no network (fetch/XHR/WebSocket/EventSource are blocked), no localStorage/sessionStorage/indexedDB, no submitting forms, no window.open.
"""


async def generate_artifact_code(settings: dict[str, Any], model: str, prompt: str) -> str:
    """Generate a fresh artifact document from a plain-language request."""
    code = await llm.complete(settings, model, [
        {"role": "system", "content": ARTIFACT_SYSTEM},
        {"role": "user", "content": f"Artifact request: {prompt.strip()}"},
    ], kind="artifact")
    return _clean_html(code)


async def revise_artifact_code(settings: dict[str, Any], model: str, code: str, instruction: str, prompt: str = "") -> str:
    """Revise an existing document. The whole document goes to the model so it can return a whole one back."""
    code = (code or "").strip()
    if not code:
        raise ValueError("Nothing to revise yet")
    if len(code) > MAX_REVISE_CHARS:
        raise ValueError(f"Artifact is {len(code)} characters, too large to revise (limit {MAX_REVISE_CHARS})")
    if not instruction.strip():
        raise ValueError("instruction required")
    origin = f"Originally asked for: {prompt.strip()}\n\n" if prompt.strip() else ""
    out = await llm.complete(settings, model, [
        {"role": "system", "content": REVISE_SYSTEM},
        {"role": "user", "content": f"{origin}Current document:\n{code}\n\nRevision: {instruction.strip()}"},
    ], kind="artifact")
    return _clean_html(out)


def _clean_html(text: str) -> str:
    """Strip the markdown fence models add anyway, and wrap a bare fragment so the iframe has a document."""
    code = re.sub(r"^```(?:html)?\s*|\s*```$", "", (text or "").strip(), flags=re.I | re.M).strip()
    if not code:
        return ""
    if "<html" not in code.lower():
        code = f"<!doctype html><html><head><meta charset=\"utf-8\"><title>Artifact</title></head><body style='margin:0;padding:16px;background:#262624;font-family:system-ui;color:#ecebe8'>{code}</body></html>"
    return code
