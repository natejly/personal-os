"""Artifacts: model-authored documents that open on the canvas beside the thread.

The model writes a fenced ```artifact block whose first line is a JSON header
({"id", "title", "kind"}); everything after it is the document. Blocks are parsed out of the
finished reply and upserted by `identifier`, so a model that rewrites the same artifact in a later
turn produces a new *version* of one row rather than a second row.

`document()` turns a stored artifact into a standalone HTML page. That page is always served into a
sandboxed iframe from the sidecar's own origin, never inlined into the renderer: a srcdoc iframe
inherits the parent's CSP, which would block every script an artifact needs.
"""
from __future__ import annotations

import html as html_mod
import json
import re
from typing import Any

from .db import Database, new_id, now, row_to_dict

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
  project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
  message_id TEXT,
  identifier TEXT NOT NULL,                 -- model-chosen slug, stable across rewrites
  title TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'html',        -- html | svg | react | markdown | code
  lang TEXT NOT NULL DEFAULT '',            -- syntax highlight hint, kind='code' only
  content TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_ident ON artifacts(IFNULL(conversation_id, ''), identifier);
CREATE INDEX IF NOT EXISTS idx_artifact_conv ON artifacts(conversation_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS artifact_versions (
  id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'html',
  lang TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL,
  message_id TEXT,
  source TEXT NOT NULL DEFAULT 'model',     -- model | user
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_av_artifact ON artifact_versions(artifact_id, version DESC);
"""

KINDS = ("html", "svg", "react", "markdown", "code")
#: Kinds whose preview is a live page in the iframe. The rest the panel renders itself.
LIVE_KINDS = ("html", "svg", "react")

_FENCE = re.compile(r"^(?P<indent>[ \t]{0,3})(?P<ticks>`{3,})[ \t]*artifact[ \t]*$", re.MULTILINE)
_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(s: str, fallback: str = "artifact") -> str:
    out = _SLUG.sub("-", (s or "").strip().lower()).strip("-")
    return (out or fallback)[:64]


def _coerce_kind(raw: Any) -> str:
    k = str(raw or "html").strip().lower()
    if k in ("jsx", "tsx", "react-component"):
        return "react"
    if k in ("md",):
        return "markdown"
    if k in ("text/html", "application/vnd.html"):
        return "html"
    if k in ("image/svg+xml",):
        return "svg"
    return k if k in KINDS else "html"


def parse(text: str) -> list[dict[str, Any]]:
    """Pull every complete ```artifact block out of one reply, in order.

    An unterminated block (the reply was cut off mid-artifact) is skipped: half a document is not
    worth a row. Later blocks with the same identifier win, so a model that corrects itself within a
    single reply persists only the correction.
    """
    out: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    pos = 0
    while True:
        m = _FENCE.search(text, pos)
        if not m:
            break
        ticks = m.group("ticks")
        body_start = m.end() + 1  # skip the newline that ends the opening fence line
        close = re.compile(rf"^[ \t]{{0,3}}{ticks}`*[ \t]*$", re.MULTILINE).search(text, body_start)
        if not close:
            break  # unterminated: nothing after it can be trusted either
        spec = _parse_block(text[body_start:close.start()])
        pos = close.end()
        if not spec:
            continue
        if spec["identifier"] not in out:
            order.append(spec["identifier"])
        out[spec["identifier"]] = spec
    return [out[i] for i in order]


def _parse_block(block: str) -> dict[str, Any] | None:
    """Split one block into its JSON header line and the document beneath it."""
    lines = block.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return None
    header: dict[str, Any] = {}
    stripped = lines[i].strip()
    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                header, i = parsed, i + 1
        except ValueError:
            pass  # not a header after all: the block is a bare document
    content = "\n".join(lines[i:]).strip("\n")
    if not content.strip():
        return None
    title = str(header.get("title") or "").strip()
    identifier = slugify(str(header.get("id") or header.get("identifier") or ""), "")
    if not identifier:
        identifier = slugify(title, "artifact")
    return {
        "identifier": identifier,
        "title": title or identifier.replace("-", " ").title(),
        "kind": _coerce_kind(header.get("kind") or header.get("type")),
        "lang": str(header.get("lang") or header.get("language") or "").strip()[:24],
        "content": content,
    }


class Artifacts:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            c.executescript(SCHEMA)

    # ---------- reads ----------
    def get(self, aid: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute("SELECT * FROM artifacts WHERE id=?", (aid,)).fetchone())

    def by_identifier(self, conversation_id: str | None, identifier: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute(
                "SELECT * FROM artifacts WHERE IFNULL(conversation_id,'')=? AND identifier=?",
                (conversation_id or "", identifier)).fetchone())

    def list(self, conversation_id: str | None = None, project_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM artifacts"
        where, args = [], []
        if conversation_id:
            where.append("conversation_id=?")
            args.append(conversation_id)
        if project_id:
            where.append("project_id=?")
            args.append(project_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(limit)
        with self.db.tx() as c:
            return [row_to_dict(r) or {} for r in c.execute(sql, args)]

    def versions(self, aid: str) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            return [row_to_dict(r) or {} for r in c.execute(
                "SELECT * FROM artifact_versions WHERE artifact_id=? ORDER BY version DESC", (aid,))]

    def version(self, aid: str, version: int) -> dict[str, Any] | None:
        with self.db.tx() as c:
            return row_to_dict(c.execute(
                "SELECT * FROM artifact_versions WHERE artifact_id=? AND version=?", (aid, version)).fetchone())

    # ---------- writes ----------
    def save(self, spec: dict[str, Any], conversation_id: str | None = None, project_id: str | None = None,
             message_id: str | None = None, source: str = "model") -> tuple[dict[str, Any], bool]:
        """Create or re-version one artifact. Returns (artifact, changed).

        `changed` is False when the content is byte-identical to the current version, which happens
        whenever a reply is re-rendered or an unchanged artifact is re-emitted: no version is burnt.
        """
        existing = self.by_identifier(conversation_id, spec["identifier"])
        ts = now()
        if existing:
            same = (existing["content"] == spec["content"] and existing["kind"] == spec["kind"]
                    and existing["title"] == spec["title"] and existing["lang"] == spec.get("lang", ""))
            if same:
                return existing, False
            version = int(existing["version"]) + 1
            with self.db.tx() as c:
                c.execute("UPDATE artifacts SET title=?, kind=?, lang=?, content=?, version=?, message_id=?, updated_at=? WHERE id=?",
                          (spec["title"], spec["kind"], spec.get("lang", ""), spec["content"], version,
                           message_id or existing["message_id"], ts, existing["id"]))
                self._add_version(c, existing["id"], version, spec, message_id, source, ts)
            return self.get(existing["id"]) or existing, True

        aid = new_id()
        with self.db.tx() as c:
            c.execute("INSERT INTO artifacts(id,conversation_id,project_id,message_id,identifier,title,kind,lang,content,version,created_at,updated_at)"
                      " VALUES(?,?,?,?,?,?,?,?,?,1,?,?)",
                      (aid, conversation_id, project_id, message_id, spec["identifier"], spec["title"],
                       spec["kind"], spec.get("lang", ""), spec["content"], ts, ts))
            self._add_version(c, aid, 1, spec, message_id, source, ts)
        return self.get(aid) or {}, True

    @staticmethod
    def _add_version(c: Any, aid: str, version: int, spec: dict[str, Any], message_id: str | None,
                     source: str, ts: float) -> None:
        c.execute("INSERT INTO artifact_versions(id,artifact_id,version,title,kind,lang,content,message_id,source,created_at)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (new_id(), aid, version, spec["title"], spec["kind"], spec.get("lang", ""),
                   spec["content"], message_id, source, ts))

    def save_from_reply(self, text: str, conversation_id: str, project_id: str | None,
                        message_id: str) -> list[dict[str, Any]]:
        """Persist every artifact in one finished reply. Returns the rows, changed or not."""
        out = []
        for spec in parse(text):
            row, _ = self.save(spec, conversation_id, project_id, message_id, "model")
            if row:
                out.append(row)
        return out

    def update(self, aid: str, patch: dict[str, Any], source: str = "user") -> dict[str, Any] | None:
        cur = self.get(aid)
        if not cur:
            return None
        spec = {
            "identifier": cur["identifier"],
            "title": str(patch.get("title", cur["title"])).strip() or cur["title"],
            "kind": _coerce_kind(patch.get("kind", cur["kind"])),
            "lang": str(patch.get("lang", cur["lang"]) or ""),
            "content": patch.get("content", cur["content"]),
        }
        row, _ = self.save(spec, cur["conversation_id"], cur["project_id"], cur["message_id"], source)
        return row

    def revert(self, aid: str, version: int) -> dict[str, Any] | None:
        """Reverting is a forward edit: it appends the old content as a new version, never rewrites history."""
        old = self.version(aid, version)
        if not old:
            return None
        return self.update(aid, {"title": old["title"], "kind": old["kind"], "lang": old["lang"],
                                 "content": old["content"]}, source="user")

    def delete(self, aid: str) -> bool:
        with self.db.tx() as c:
            return c.execute("DELETE FROM artifacts WHERE id=?", (aid,)).rowcount > 0


# ---------------- rendering ----------------

BASE_CSS = """
*,*::before,*::after{box-sizing:border-box}
html,body{margin:0;padding:0}
html{color-scheme:light dark}
body{font:14px/1.55 -apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',system-ui,sans-serif;
  color:#1c1b19;background:#fff;-webkit-font-smoothing:antialiased}
:root[data-theme='dark'] body{color:#e8e6e1;background:#1a1a18}
:root[data-theme='dark']{color-scheme:dark}
a{color:#c15f3c}
:root[data-theme='dark'] a{color:#e08a67}
.artifact-pad{padding:20px}
.artifact-center{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.artifact-center>svg{max-width:100%;height:auto}
pre.artifact-code{margin:0;padding:20px;white-space:pre-wrap;word-break:break-word;
  font:12.5px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}
"""

CDN_REACT = "https://unpkg.com/react@18/umd/react.production.min.js"
CDN_REACT_DOM = "https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"
CDN_BABEL = "https://cdnjs.cloudflare.com/ajax/libs/babel-standalone/7.24.7/babel.min.js"
CDN_TAILWIND = "https://cdn.tailwindcss.com/3.4.3"

#: An artifact iframe is its own browsing context and inherits none of the renderer's CSP, so it
#: gets one here. `connect-src 'none'` and a data-only `img-src` are the important lines: an
#: artifact may quote untrusted text the model read, and neither fetch() nor an <img> beacon can
#: carry it off the machine. Scripts are allowed only from the three CDNs the wrappers use.
CSP = ("default-src 'none'; "
       f"script-src 'unsafe-inline' 'unsafe-eval' {CDN_REACT} {CDN_REACT_DOM} {CDN_BABEL} https://cdn.tailwindcss.com; "
       "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; "
       "connect-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'")

_IMPORT = re.compile(r"^[ \t]*import[ \t].*?(?:;|$)\n?", re.MULTILINE)
_EXPORT_DEFAULT_DECL = re.compile(r"^[ \t]*export[ \t]+default[ \t]+(?=(?:async[ \t]+)?(?:function|class)\b)", re.MULTILINE)
_EXPORT_DEFAULT_EXPR = re.compile(r"^[ \t]*export[ \t]+default[ \t]+", re.MULTILINE)
_EXPORT = re.compile(r"^[ \t]*export[ \t]+(?=(?:const|let|var|function|class|async)\b)", re.MULTILINE)

NL = "\n"


def _script_safe(code: str) -> str:
    """`</script>` anywhere in the body would close the tag early, whatever the surrounding quotes."""
    return code.replace("</script", "<\\/script").replace("<!--", "<\\!--")


def _shell(title: str, theme: str, head: str, body: str) -> str:
    t = "dark" if theme == "dark" else "light"
    return ('<!doctype html><html lang="en" data-theme="' + t + '"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>' + html_mod.escape(title or "Artifact") + '</title>'
            '<style>' + BASE_CSS + '</style>' + head + '</head><body>' + body + '</body></html>')


def _strip_modules(code: str) -> str:
    """JSX with no bundler and no module graph: drop the imports, keep the declarations."""
    code = _IMPORT.sub("", code)
    code = _EXPORT_DEFAULT_DECL.sub("", code)          # export default function App() -> function App()
    code = _EXPORT_DEFAULT_EXPR.sub("var __default__ = ", code)  # export default () => ... -> var __default__ = ...
    return _EXPORT.sub("", code)


#: Every candidate is probed with `typeof` first: naming one that the artifact never declared would
#: otherwise throw a ReferenceError before the mount runs.
_MOUNT = """
var __names = [
  typeof App !== 'undefined' ? App : null,
  typeof __default__ !== 'undefined' ? __default__ : null,
  typeof Component !== 'undefined' ? Component : null,
  typeof Main !== 'undefined' ? Main : null,
  typeof Page !== 'undefined' ? Page : null
];
var __C = __names.filter(function (c) { return typeof c === 'function' })[0];
var __el = document.getElementById('root');
if (__C) ReactDOM.createRoot(__el).render(React.createElement(__C));
else __el.innerHTML = '<div class="artifact-pad">Nothing to render. Define <code>function App()</code> and this will mount it.</div>';
"""

_PRELUDE = ("const {useState,useEffect,useRef,useMemo,useCallback,useReducer,useContext,"
            "createContext,Fragment,memo,forwardRef} = React;")


def _react_page(code: str, title: str, theme: str) -> str:
    """Babel standalone compiles the component in place and mounts it.

    User code and mount share one <script> on purpose: Babel evaluates each text/babel block
    separately, and a top-level `const App = ...` in one block is not visible from the next.
    """
    head = ('<script src="' + CDN_TAILWIND + '"></script>'
            '<script crossorigin src="' + CDN_REACT + '"></script>'
            '<script crossorigin src="' + CDN_REACT_DOM + '"></script>'
            '<script src="' + CDN_BABEL + '"></script>')
    script = _script_safe(_PRELUDE + NL + _strip_modules(code) + NL + _MOUNT)
    body = '<div id="root"></div><script type="text/babel" data-presets="react">' + script + '</script>'
    return _shell(title, theme, head, body)


def document(kind: str, content: str, title: str = "", theme: str = "light", lang: str = "") -> str:
    """One artifact as a standalone HTML page, ready to be served into a sandboxed iframe."""
    kind = _coerce_kind(kind)
    if kind == "react":
        return _react_page(content, title, theme)
    if kind == "svg":
        return _shell(title, theme, "", '<div class="artifact-center">' + content + '</div>')
    if kind == "html":
        if re.match(r"<!doctype|<html\b", content.lstrip()[:200], re.IGNORECASE):
            return content  # a complete document: the model's own <head> wins
        head = '<script src="' + CDN_TAILWIND + '"></script>'
        return _shell(title, theme, head, '<div class="artifact-pad">' + content + '</div>')
    # markdown / code: the panel renders these itself; this page is the pop-out / browser fallback
    return _shell(title, theme, "", '<pre class="artifact-code">' + html_mod.escape(content) + '</pre>')
