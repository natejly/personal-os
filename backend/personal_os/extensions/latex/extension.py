"""LaTeX as a built-in extension. A LaTeX source is a Files doc whose title ends in .tex, .bib, .sty or .cls; a LaTeX
project is a Files folder holding main.tex (or the one .tex with \\documentclass), with its subfolders as relative paths.
Images and other binary files live apart, in the extension's data folder. Compiling copies the project's docs and assets
into a build folder there and runs latexmk, never with shell escape or a project rc file (both are Perl or TeX that run).
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import signal
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ...docs import folder_path, is_latex_source
from ...tools import ToolSpec, _obj, tool_error

if TYPE_CHECKING:
    from ...db import Database
    from ...docs import Docs
    from ..host import ExtensionScope

log = logging.getLogger(__name__)
BUILD_DIR = ".grain-build"
ENGINES = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}
UPLOAD_CAP = 25 * 1024 * 1024
COMPILE_TIMEOUT = 120
MAX_DIAGNOSTICS = 50
TEX_BIN = "/Library/TeX/texbin"
TEMPLATE = r"""\documentclass{article}
\usepackage{amsmath}

\title{Untitled}
\author{}
\date{\today}

\begin{document}
\maketitle

\section{Introduction}
Write here.

\end{document}
"""


class LatexError(Exception):
    pass


# ---- names and paths ----
def safe_name(name: str) -> str:
    """A file name that stays a plain file inside the build folder and cannot reach latexmk as an option."""
    if not name or name != name.strip() or re.search(r"[/\\\x00-\x1f]", name) or name.startswith((".", "-")) or len(name) > 200:
        raise LatexError(f"'{name}' is not a usable LaTeX file name")
    return name


def safe_rel(rel: str) -> Path:
    """A relative path of safe segments (an asset name, or a doc's folder-relative path)."""
    p = Path(rel.replace("\\", "/"))
    if not rel or p.is_absolute() or any(not seg or seg in (".", "..") for seg in p.parts):
        raise LatexError(f"Path '{rel}' is not allowed")
    for seg in p.parts:
        safe_name(seg)
    return p


def inside(root: Path, p: Path) -> bool:
    return p.resolve().is_relative_to(root.resolve())


def project_key(scope: str, folder: str) -> str:
    return hashlib.sha1(f"{scope}\0{folder}".encode()).hexdigest()[:16]


# ---- log parser ----
FILE_LINE = re.compile(r"^(?:\./)?([^\s:][^:]*\.\w+):(\d+): (.*)$")
BANG = re.compile(r"^! (.*)$")
L_LINE = re.compile(r"^l\.(\d+)")
BOX = re.compile(r"^(?:Overfull|Underfull) \\[hv]box .*? lines? (\d+)")
WARN = re.compile(r"^(?:LaTeX|Package \S+|Class \S+) Warning: (.*)$")
ON_LINE = re.compile(r"on input line (\d+)")


def parse_log(log: str, main: str = "main.tex") -> dict[str, list[dict[str, Any]]]:
    """Errors and warnings from a TeX log: {file, line, message} each, capped. `file:line: msg` lines (from
    -file-line-error) give the file; a bare `! msg` block takes its line from the following `l.<n>` and the main file."""
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    lines = log.splitlines()
    for i, ln in enumerate(lines):
        if m := FILE_LINE.match(ln):
            errors.append({"file": m[1], "line": int(m[2]), "message": m[3].strip()})
        elif (m := BANG.match(ln)) and not m[1].startswith(("Emergency stop", "==>")):
            n = next((L_LINE.match(a) for a in lines[i + 1:i + 8] if L_LINE.match(a)), None)
            if n:
                errors.append({"file": main, "line": int(n[1]), "message": m[1].strip()})
        elif m := BOX.match(ln):
            warnings.append({"file": "", "line": int(m[1]), "message": ln.strip()})
        elif m := WARN.match(ln):
            text = m[1]
            if not ON_LINE.search(text) and i + 1 < len(lines):
                text += " " + lines[i + 1].strip()
            n = ON_LINE.search(text)
            warnings.append({"file": "", "line": int(n[1]) if n else 0, "message": text.strip()})
    seen: set[tuple[Any, ...]] = set()
    uniq = []
    for e in errors:
        k = (e["file"], e["line"], e["message"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return {"errors": uniq[:MAX_DIAGNOSTICS], "warnings": warnings[:MAX_DIAGNOSTICS]}


def tex_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([env.get("PATH", ""), TEX_BIN, "/usr/local/bin"])  # a GUI app starts with a thin PATH
    env["openout_any"] = "p"  # paranoid: TeX may not write outside the build folder
    return env


class Latex:
    """The projects of one Grain database. `grain_projects` maps a Grain project id to its name."""

    def __init__(self, docs: Docs, db: Database, data_dir: Path, grain_projects: Callable[[], dict[str, str]]) -> None:
        self.docs, self.db, self.data_dir, self.grain_projects = docs, db, data_dir, grain_projects
        self._locks: dict[str, asyncio.Lock] = {}

    # ---- which docs belong to a project ----
    def resolve_scope(self, ref: str | None) -> str:
        """A Grain project id, or a name (any case), as a scope; '' for Personal. An unknown one is an error."""
        if not ref:
            return ""
        names = self.grain_projects()
        if ref in names:
            return ref
        hit = [i for i, n in names.items() if n.lower() == ref.strip().lower()]
        if len(hit) != 1:
            raise LatexError(f"No Grain project '{ref}'")
        return hit[0]

    def sources(self, scope: str) -> list[dict[str, Any]]:
        return [d for d in self.docs.list(scope or None) if is_latex_source(d["title"])]

    @staticmethod
    def in_tree(d: dict[str, Any], folder: str) -> bool:
        f = d["folder"] or ""
        return f == folder if not folder else f == folder or f.startswith(folder + "/")

    def main_of(self, scope: str, folder: str, rows: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        """The main doc of a folder: main.tex, else the one .tex with \\documentclass; None when the folder is no project."""
        direct = [d for d in (rows if rows is not None else self.sources(scope)) if (d["folder"] or "") == folder and d["title"].lower().endswith(".tex")]
        for d in direct:
            if d["title"] == "main.tex":
                return d
        withclass = [d for d in direct if "\\documentclass" in ((self.docs.get(d["id"]) or {}).get("content") or "")]
        return withclass[0] if len(withclass) == 1 else None

    def project_for(self, doc_id: str) -> dict[str, Any]:
        """The project a LaTeX doc belongs to: the nearest folder above it with a main doc; a .tex with none is its own project."""
        d = self.docs.get(doc_id)
        if not d or not is_latex_source(d["title"]):
            raise LatexError("That is not a LaTeX source file")
        scope, folder = d["project_id"] or "", d["folder"] or ""
        rows = self.sources(scope)
        f = folder
        while True:
            if main := self.main_of(scope, f, rows):
                return self.describe(scope, f, main, rows)
            if not f:
                break
            f = f.rsplit("/", 1)[0] if "/" in f else ""
        if d["title"].lower().endswith(".tex"):
            return self.describe(scope, folder, d, rows)
        raise LatexError("This file is not inside a LaTeX project (a folder with a main.tex)")

    def synced(self, doc_id: str) -> dict[str, Any]:
        """project_for, after any source files left in its asset folder by older uploads have become docs."""
        p = self.project_for(doc_id)
        return self.project_for(doc_id) if self.convert_assets(p["scope"], p["folder"]) else p

    def project_at(self, scope: str, folder: str) -> dict[str, Any]:
        rows = self.sources(scope)
        main = self.main_of(scope, folder, rows)
        if not main:
            raise LatexError(f"'{folder or 'the top level'}' has no main.tex")
        return self.describe(scope, folder, main, rows)

    def engine_of(self, scope: str, folder: str) -> str:
        with self.db.tx() as c:
            r = c.execute("SELECT engine FROM latex_projects WHERE scope=? AND folder=?", (scope, folder)).fetchone()
        return r["engine"] if r and r["engine"] in ENGINES else "pdflatex"

    def set_engine(self, scope: str, folder: str, engine: str) -> None:
        if engine not in ENGINES:
            raise LatexError(f"Unknown engine '{engine}' (pdflatex, xelatex or lualatex)")
        with self.db.tx() as c:
            c.execute("INSERT INTO latex_projects(scope, folder, engine) VALUES(?,?,?) ON CONFLICT(scope, folder) DO UPDATE SET engine=excluded.engine",
                      (scope, folder, engine))

    def build_dir(self, scope: str, folder: str) -> Path:
        return self.data_dir / "build" / project_key(scope, folder)

    def asset_dir(self, scope: str, folder: str) -> Path:
        return self.data_dir / "assets" / project_key(scope, folder)

    def describe(self, scope: str, folder: str, main: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
        tree = [d for d in rows if self.in_tree(d, folder)]
        names = self.grain_projects()
        return {"scope": scope, "folder": folder, "name": folder.rsplit("/", 1)[-1] or "Files",
                "project_id": scope or None, "project": names.get(scope), "main_doc_id": main["id"], "main": main["title"],
                "engine": self.engine_of(scope, folder), "has_pdf": (self.build_dir(scope, folder) / (Path(main["title"]).stem + ".pdf")).is_file(),
                "updated_at": max((d["updated_at"] for d in tree), default=main["updated_at"])}

    def list_projects(self, project: str | None = None) -> list[dict[str, Any]]:
        scope_filter = self.resolve_scope(project) if project else None
        rows = [d for d in self.docs.list("__all__") if is_latex_source(d["title"])]
        out = []
        for scope, folder in sorted({(d["project_id"] or "", d["folder"] or "") for d in rows if d["title"].lower().endswith(".tex")}):
            if scope_filter is not None and scope != scope_filter:
                continue
            scoped = [d for d in rows if (d["project_id"] or "") == scope]
            if main := self.main_of(scope, folder, scoped):
                out.append(self.describe(scope, folder, main, scoped))
        return out

    def context(self, doc_id: str) -> dict[str, Any]:
        """What the editor shows beside a doc: its project, the project's source docs by relative path, and its assets."""
        proj = self.synced(doc_id)
        folder = proj["folder"]
        files = []
        for d in self.sources(proj["scope"]):
            if self.in_tree(d, folder):
                rest = (d["folder"] or "")[len(folder):].lstrip("/")
                files.append({"doc_id": d["id"], "path": f"{rest}/{d['title']}" if rest else d["title"], "kind": "bib" if d["title"].lower().endswith(".bib") else "tex"})
        files.sort(key=lambda f: f["path"])
        return {"project": proj, "files": files, "assets": self.assets(proj["scope"], folder)}

    def create(self, name: str, project: str | None = None) -> dict[str, Any]:
        scope = self.resolve_scope(project)
        if re.search(r"[/\\]", name or ""):
            raise LatexError("A project name has no slash")
        base = folder_path(name)
        if not base:
            raise LatexError("A project needs a name")
        taken = {f["path"] for f in self.docs.folders() if f["scope"] == scope}
        folder, n = base, 1
        while folder in taken:
            n += 1
            folder = f"{base} {n}"
        self.docs.create_folder(folder, scope)
        main = self.docs.create("main.tex", TEMPLATE, scope or None, folder)
        return self.describe(scope, folder, main, self.sources(scope))

    # ---- assets ----
    def assets(self, scope: str, folder: str) -> list[dict[str, Any]]:
        root = self.asset_dir(scope, folder)
        if not root.is_dir():
            return []
        return [{"path": p.relative_to(root).as_posix(), "size": p.stat().st_size} for p in sorted(root.rglob("*")) if p.is_file() and inside(root, p)]

    def put_asset(self, scope: str, folder: str, rel: str, data: bytes) -> dict[str, Any]:
        root = self.asset_dir(scope, folder)
        dest = root / safe_rel(rel)
        if len(data) > UPLOAD_CAP:
            raise LatexError("File is too large to upload")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not inside(root, dest):  # a symlink planted in the folder
            raise LatexError(f"Path '{rel}' leaves the project")
        dest.write_bytes(data)
        return {"path": dest.relative_to(root).as_posix(), "size": len(data)}

    @staticmethod
    def decode(data: bytes) -> str:
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return data.decode("latin-1")  # old TeX sources are often 8-bit; better readable than refused

    @staticmethod
    def is_template(text: str) -> bool:
        """The untouched new-project template (its date line differs by version, so ignore it)."""
        date = re.compile(r"\\date\{[^}]*\}")
        return date.sub("", text).strip() == date.sub("", TEMPLATE).strip()

    def import_source(self, scope: str, folder: str, rel: str, data: bytes) -> dict[str, Any]:
        """File an uploaded LaTeX source as a doc in the project folder (+ relative subpath). A doc already there is replaced only
        while it is the untouched template; otherwise the upload becomes `<stem> (uploaded)<ext>`. Never overwrites user text."""
        p = safe_rel(rel)
        if len(data) > UPLOAD_CAP:
            raise LatexError("File is too large to upload")
        dest = "/".join(x for x in (folder, p.parent.as_posix() if p.parent.parts else "") if x)
        text = self.decode(data)
        taken = {d["title"]: d for d in self.docs.list(scope or None) if (d["folder"] or "") == dest}
        title = p.name
        if title in taken:
            cur = self.docs.get(taken[title]["id"]) or {}
            if self.is_template(cur.get("content") or ""):
                d = self.docs.save(cur["id"], text, summary="Uploaded")
                return {"kind": "doc", "doc_id": d["id"], "path": (p.parent / title).as_posix(), "replaced": True}
            stem, ext = os.path.splitext(title)
            n = 1
            while title in taken:
                title = f"{stem} (uploaded{'' if n == 1 else f' {n}'}){ext}"
                n += 1
        d = self.docs.create(title, text, scope or None, dest)
        return {"kind": "doc", "doc_id": d["id"], "path": (p.parent / title).as_posix(), "replaced": False}

    def upload(self, scope: str, folder: str, rel: str, data: bytes) -> dict[str, Any]:
        if is_latex_source(Path(rel).name):
            return self.import_source(scope, folder, rel, data)
        return {"kind": "asset", **self.put_asset(scope, folder, rel, data)}

    def convert_assets(self, scope: str, folder: str) -> int:
        """Older uploads put .tex/.bib files in the asset folder, where they cannot be opened or edited. Turn each into a doc, and
        remove the asset only once the doc is written. A failure on one file is logged and the rest go on. Idempotent."""
        done = 0
        root = self.asset_dir(scope, folder)
        for a in self.assets(scope, folder):
            if not is_latex_source(Path(a["path"]).name):
                continue
            try:
                self.import_source(scope, folder, a["path"], (root / a["path"]).read_bytes())
                self.delete_asset(scope, folder, a["path"])
                done += 1
            except Exception as e:  # noqa: BLE001 - the asset stays; try again next time
                log.warning("latex: could not convert asset %s: %s", a["path"], e)
        return done

    def delete_asset(self, scope: str, folder: str, rel: str) -> None:
        root = self.asset_dir(scope, folder)
        p = root / safe_rel(rel)
        if inside(root, p) and p.is_file():
            p.unlink()

    # ---- compile ----
    def materialize(self, proj: dict[str, Any]) -> tuple[Path, dict[str, str]]:
        """Write the project's docs and assets into its build folder (keeping latexmk's own output); returns it and {path: doc id}."""
        scope, folder = proj["scope"], proj["folder"]
        out = self.build_dir(scope, folder)
        out.mkdir(parents=True, exist_ok=True)
        for p in out.iterdir():
            if p.name != BUILD_DIR:
                shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
        ids: dict[str, str] = {}
        for d in self.sources(scope):
            if not self.in_tree(d, folder):
                continue
            rest = (d["folder"] or "")[len(folder):].lstrip("/")
            rel = safe_rel(f"{rest}/{safe_name(d['title'])}" if rest else safe_name(d["title"]))
            dest = out / rel
            if not inside(out, dest.parent):
                raise LatexError(f"'{d['title']}' would leave the build folder")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text((self.docs.get(d["id"]) or {}).get("content") or "")
            ids[rel.as_posix()] = d["id"]
        assets = self.asset_dir(scope, folder)
        for a in self.assets(scope, folder):
            dest = out / safe_rel(a["path"])
            if dest.exists():
                continue  # a doc of the same path wins
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(assets / a["path"], dest)
        return out, ids

    async def compile(self, proj: dict[str, Any], engine: str | None = None) -> dict[str, Any]:
        scope, folder = proj["scope"], proj["folder"]
        if engine:
            self.set_engine(scope, folder, engine)
        env = tex_env()
        latexmk = shutil.which("latexmk", path=env["PATH"])
        if not latexmk:
            raise LatexError("TeX is not installed: latexmk was not found. Install TeX Live or MacTeX.")
        main = safe_name(proj["main"])
        async with self._locks.setdefault(project_key(scope, folder), asyncio.Lock()):
            t0 = time.monotonic()
            build, ids = self.materialize(proj)
            cmd = [latexmk, "-norc", "-no-shell-escape", ENGINES[self.engine_of(scope, folder)], "-interaction=nonstopmode", "-halt-on-error",
                   "-file-line-error", "-synctex=1", f"-outdir={BUILD_DIR}", main]
            p = await asyncio.create_subprocess_exec(*cmd, cwd=build, env=env, stdin=asyncio.subprocess.DEVNULL,
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
            timed_out = False
            try:
                out, _ = await asyncio.wait_for(p.communicate(), COMPILE_TIMEOUT)
            except asyncio.TimeoutError:
                timed_out = True
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                out, _ = await p.communicate()
            stem = Path(main).stem
            log_file = build / BUILD_DIR / f"{stem}.log"
            log = log_file.read_text(errors="replace") if log_file.is_file() else out.decode(errors="replace")
            parsed = parse_log(log, main)
            built = build / BUILD_DIR / f"{stem}.pdf"
            ok = p.returncode == 0 and not timed_out and built.is_file()
            if ok:
                shutil.copyfile(built, build / f"{stem}.pdf")
            elif timed_out:
                parsed["errors"].insert(0, {"file": main, "line": 0, "message": f"Compile timed out after {COMPILE_TIMEOUT} s"})
            elif not parsed["errors"]:
                parsed["errors"].append({"file": main, "line": 0, "message": "Compile failed; see the log"})
            for e in parsed["errors"]:
                e["doc_id"] = ids.get(e["file"])
            return {"ok": ok, "pdf": (build / f"{stem}.pdf").is_file(), **parsed, "main_doc_id": proj["main_doc_id"],
                    "log_tail": "\n".join(log.splitlines()[-40:]), "duration_ms": int((time.monotonic() - t0) * 1000)}

    def pdf_path(self, proj: dict[str, Any]) -> Path:
        p = self.build_dir(proj["scope"], proj["folder"]) / (Path(proj["main"]).stem + ".pdf")
        if not p.is_file():
            raise LatexError("Not compiled yet")
        return p


class NewProject(BaseModel):
    name: str
    project_id: str | None = None


class CompileIn(BaseModel):
    doc_id: str
    engine: str | None = None


def router(lx: Latex) -> APIRouter:
    r = APIRouter(prefix="/latex")

    def guard(fn: Any, *a: Any, **kw: Any) -> Any:
        try:
            return fn(*a, **kw)
        except LatexError as e:
            raise HTTPException(400, str(e)) from e

    @r.get("/projects")
    def projects(project_id: str | None = None) -> list[dict[str, Any]]:
        return guard(lx.list_projects, project_id)

    @r.post("/projects")
    def new_project(body: NewProject) -> dict[str, Any]:
        return guard(lx.create, body.name, body.project_id)

    @r.get("/context")
    def context(doc_id: str) -> dict[str, Any]:
        return guard(lx.context, doc_id)

    @r.post("/compile")
    async def compile_(body: CompileIn) -> dict[str, Any]:
        try:
            return await lx.compile(lx.synced(body.doc_id), body.engine)
        except LatexError as e:
            raise HTTPException(400, str(e)) from e

    @r.get("/pdf")
    def pdf(doc_id: str) -> FileResponse:
        p = guard(lambda: lx.pdf_path(lx.project_for(doc_id)))
        return FileResponse(p, media_type="application/pdf", headers={"Cache-Control": "no-store"})

    @r.post("/assets")
    async def upload(doc_id: str, file: UploadFile, path: str | None = None) -> dict[str, Any]:
        proj = guard(lx.synced, doc_id)
        data = await file.read(UPLOAD_CAP + 1)
        return guard(lx.upload, proj["scope"], proj["folder"], path or Path(file.filename or "").name, data)

    @r.delete("/assets")
    def delete_asset(doc_id: str, path: str) -> dict[str, bool]:
        proj = guard(lx.project_for, doc_id)
        guard(lx.delete_asset, proj["scope"], proj["folder"], path)
        return {"ok": True}

    return r


def register_tools(api: ExtensionScope, lx: Latex) -> None:
    async def latex_projects(ctx: dict[str, Any], project: str | None = None) -> Any:
        try:
            rows = lx.list_projects(project)
        except LatexError as e:
            return tool_error(str(e), field="project")
        return {"projects": [{k: p[k] for k in ("name", "folder", "project_id", "project", "main_doc_id", "engine", "has_pdf")} for p in rows]}
    api.tool(ToolSpec("latex_projects", "List the user's LaTeX projects (Files folders with a main.tex): folder, Grain project, main doc id. Edit their files with the doc tools.",
                      _obj({"project": {"type": "string", "description": "only this Grain project (id or name)"}}, []), latex_projects, "latex", examples=[{}]))

    async def latex_new(ctx: dict[str, Any], name: str, project: str | None = None) -> Any:
        try:
            p = lx.create(name, project or ctx.get("project_id"))  # in a project's chat, the document joins that project
        except LatexError as e:
            return tool_error(str(e), field="name", example={"name": "thesis"})
        return {k: p[k] for k in ("name", "folder", "project_id", "project", "main_doc_id")}
    api.tool(ToolSpec("latex_new", "Create a LaTeX project: a Files folder with a minimal main.tex, in a Grain project (default: this chat's).",
                      _obj({"name": {"type": "string"}, "project": {"type": "string", "description": "Grain project id or name"}}, ["name"]),
                      latex_new, "latex", "writes", examples=[{"name": "thesis"}]))

    async def latex_compile(ctx: dict[str, Any], doc_id: str | None = None, folder: str | None = None, project: str | None = None) -> Any:
        try:
            if doc_id:
                proj = lx.synced(doc_id)
            elif folder:
                proj = lx.project_at(lx.resolve_scope(project or ctx.get("project_id")), folder_path(folder))
            else:
                return tool_error("Pass doc_id (any file of the project) or folder", field="doc_id", example={"doc_id": "abc123"})
            res = await lx.compile(proj)
        except LatexError as e:
            return tool_error(str(e), field="doc_id", alternative="latex_projects to list projects")
        res["errors"] = res["errors"][:10]
        res["warnings"] = res["warnings"][:5]
        res["log_tail"] = "" if res["ok"] else "\n".join(res["log_tail"].splitlines()[-15:])
        return res
    api.tool(ToolSpec("latex_compile", "Compile a LaTeX project, by any of its file ids or by folder (+ project). On errors, fix them with doc_edit on the doc named in `errors` (doc_id, line), then compile again.",
                      _obj({"doc_id": {"type": "string"}, "folder": {"type": "string"}, "project": {"type": "string", "description": "Grain project id or name, with folder"}}, []),
                      latex_compile, "latex", "executes", examples=[{"doc_id": "abc123"}]))


def activate(api: ExtensionScope) -> None:
    def names() -> dict[str, str]:
        with api.db.tx() as c:
            return {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM projects WHERE deleted_at IS NULL")}
    lx = Latex(api.services.get("docs"), api.db, api.data_dir, names)  # type: ignore[arg-type]
    api.router(router(lx))
    register_tools(api, lx)
