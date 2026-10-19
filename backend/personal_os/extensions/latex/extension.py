"""LaTeX projects as a built-in extension. A project is a real folder, ~/Grain/LaTeX/<name>/, so the chat's file tools
read and edit the same files the editor shows. Compiling runs latexmk in the project folder, never with shell escape.

Every file route resolves its path inside the project folder and refuses anything that leaves it (`..`, absolute
paths, hidden names, symlinks pointing outside).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ...tools import ToolSpec, _obj, tool_error

if TYPE_CHECKING:
    from ..host import ExtensionScope

BUILD_DIR = ".grain-build"
SETTINGS_FILE = ".grain-latex.json"
ENGINES = {"pdflatex": "-pdf", "xelatex": "-xelatex", "lualatex": "-lualatex"}
TEXT_CAP = 5 * 1024 * 1024
UPLOAD_CAP = 25 * 1024 * 1024
COMPILE_TIMEOUT = 120
MAX_DIAGNOSTICS = 50
HIDDEN_SUFFIXES = (".aux", ".log", ".fls", ".fdb_latexmk", ".out", ".toc", ".synctex.gz", ".bbl", ".blg")
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


def root_dir() -> Path:
    return Path(os.environ.get("GRAIN_LATEX_DIR") or Path.home() / "Grain" / "LaTeX")


class LatexError(Exception):
    pass


# ---- storage and path safety ----
def valid_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 64 or name.startswith(".") or re.search(r"[/\\\x00-\x1f]", name):
        raise LatexError("A project name is 1-64 characters, with no slash and no leading dot")
    return name


def project_dir(name: str) -> Path:
    base = root_dir().resolve()
    d = (base / valid_name(name)).resolve()
    if d.parent != base or not d.is_dir():
        raise LatexError(f"No LaTeX project '{name}'")
    return d


def safe_path(proj: Path, rel: str) -> Path:
    """`rel` resolved inside `proj`; anything that escapes it (or names a hidden file) is refused."""
    p = Path(rel)
    if not rel or p.is_absolute() or any(part.startswith(".") for part in p.parts):
        raise LatexError(f"Path '{rel}' is not allowed")
    out = (proj / p).resolve()
    if not out.is_relative_to(proj.resolve()):
        raise LatexError(f"Path '{rel}' leaves the project")
    return out


def read_settings(proj: Path) -> dict[str, Any]:
    try:
        s = json.loads((proj / SETTINGS_FILE).read_text())
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def write_settings(proj: Path, patch: dict[str, Any]) -> None:
    (proj / SETTINGS_FILE).write_text(json.dumps({**read_settings(proj), **patch}))


def main_file(proj: Path) -> str:
    """The configured main file; else main.tex; else the one top-level .tex holding \\documentclass."""
    want = read_settings(proj).get("main")
    if isinstance(want, str) and (proj / want).is_file():
        return want
    if (proj / "main.tex").is_file():
        return "main.tex"
    docs = [f.name for f in sorted(proj.glob("*.tex")) if "\\documentclass" in f.read_text(errors="replace")]
    return docs[0] if len(docs) == 1 else "main.tex"


def engine_of(proj: Path) -> str:
    e = read_settings(proj).get("engine")
    return e if e in ENGINES else "pdflatex"


def describe(proj: Path) -> dict[str, Any]:
    main = main_file(proj)
    return {"name": proj.name, "path": str(proj), "main": main, "engine": engine_of(proj),
            "updated_at": proj.stat().st_mtime, "has_pdf": (proj / (Path(main).stem + ".pdf")).is_file()}


def list_projects() -> list[dict[str, Any]]:
    base = root_dir()
    if not base.is_dir():
        return []
    return [describe(d) for d in sorted(base.iterdir()) if d.is_dir() and not d.name.startswith(".")]


def create_project(name: str) -> dict[str, Any]:
    name = valid_name(name)
    d = root_dir() / name
    if d.exists():
        raise LatexError(f"A project named '{name}' already exists")
    d.mkdir(parents=True)
    (d / "main.tex").write_text(TEMPLATE)
    return describe(d)


def file_kind(name: str) -> str:
    s = Path(name).suffix.lower()
    return "tex" if s in (".tex", ".sty", ".cls") else "bib" if s == ".bib" else \
        "image" if s in (".png", ".jpg", ".jpeg", ".pdf", ".svg", ".eps") else "other"


def list_files(proj: Path) -> list[dict[str, Any]]:
    out = []
    for dirpath, dirs, files in os.walk(proj):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            p = Path(dirpath, f)
            if f.startswith(".") or f.endswith(HIDDEN_SUFFIXES) or not p.resolve().is_relative_to(proj.resolve()):
                continue
            rel = p.relative_to(proj).as_posix()
            out.append({"path": rel, "size": p.stat().st_size, "kind": file_kind(rel)})
    return out


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


# ---- compile ----
def tex_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([env.get("PATH", ""), TEX_BIN, "/usr/local/bin"])  # a GUI app starts with a thin PATH
    env["openout_any"] = "p"  # paranoid: TeX may not write outside the project
    return env


_locks: dict[str, asyncio.Lock] = {}


async def compile_project(name: str, engine: str | None = None) -> dict[str, Any]:
    proj = project_dir(name)
    if engine:
        if engine not in ENGINES:
            raise LatexError(f"Unknown engine '{engine}' (pdflatex, xelatex or lualatex)")
        if engine != engine_of(proj):
            write_settings(proj, {"engine": engine})
    env = tex_env()
    latexmk = shutil.which("latexmk", path=env["PATH"])
    if not latexmk:
        raise LatexError("TeX is not installed: latexmk was not found. Install TeX Live or MacTeX.")
    main = main_file(proj)
    if not (proj / main).is_file():
        raise LatexError(f"The main file '{main}' does not exist")
    async with _locks.setdefault(proj.name, asyncio.Lock()):
        t0 = time.monotonic()
        cmd = [latexmk, ENGINES[engine_of(proj)], "-interaction=nonstopmode", "-halt-on-error", "-file-line-error",
               "-synctex=1", f"-outdir={BUILD_DIR}", main]
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=proj, env=env, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                    start_new_session=True)
        timed_out = False
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), COMPILE_TIMEOUT)
        except asyncio.TimeoutError:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            out, _ = await proc.communicate()
        stem = Path(main).stem
        log_file = proj / BUILD_DIR / f"{stem}.log"
        log = log_file.read_text(errors="replace") if log_file.is_file() else out.decode(errors="replace")
        parsed = parse_log(log, main)
        built = proj / BUILD_DIR / f"{stem}.pdf"
        ok = proc.returncode == 0 and not timed_out and built.is_file()
        if ok:
            shutil.copyfile(built, proj / f"{stem}.pdf")
        elif timed_out:
            parsed["errors"].insert(0, {"file": main, "line": 0, "message": f"Compile timed out after {COMPILE_TIMEOUT} s"})
        elif not parsed["errors"]:
            parsed["errors"].append({"file": main, "line": 0, "message": "Compile failed; see the log"})
        return {"ok": ok, "pdf": (proj / f"{stem}.pdf").is_file(), **parsed,
                "log_tail": "\n".join(log.splitlines()[-40:]), "duration_ms": int((time.monotonic() - t0) * 1000)}


class NewProject(BaseModel):
    name: str


class CompileIn(BaseModel):
    engine: str | None = None


class FileIn(BaseModel):
    content: str


def guard(fn: Any, *a: Any, **kw: Any) -> Any:
    try:
        return fn(*a, **kw)
    except LatexError as e:
        raise HTTPException(404 if str(e).startswith("No LaTeX project") else 400, str(e)) from e


def router() -> APIRouter:
    r = APIRouter(prefix="/latex")

    @r.get("/projects")
    def projects() -> list[dict[str, Any]]:
        return list_projects()

    @r.post("/projects")
    def new_project(body: NewProject) -> dict[str, Any]:
        return guard(create_project, body.name)

    @r.get("/projects/{name}/files")
    def files(name: str) -> list[dict[str, Any]]:
        return list_files(guard(project_dir, name))

    @r.get("/projects/{name}/file")
    def read_file(name: str, path: str) -> dict[str, Any]:
        p = guard(safe_path, guard(project_dir, name), path)
        if not p.is_file():
            raise HTTPException(404, f"No file '{path}'")
        if p.stat().st_size > TEXT_CAP:
            raise HTTPException(413, "File is too large to edit")
        return {"path": path, "content": p.read_text(errors="replace")}

    @r.put("/projects/{name}/file")
    def write_file(name: str, path: str, body: FileIn) -> dict[str, Any]:
        p = guard(safe_path, guard(project_dir, name), path)
        data = body.content.encode()
        if len(data) > TEXT_CAP:
            raise HTTPException(413, "File is too large to save")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return {"ok": True, "size": len(data)}

    @r.post("/projects/{name}/upload")
    async def upload(name: str, file: UploadFile, path: str | None = None) -> dict[str, Any]:
        proj = guard(project_dir, name)
        rel = path or Path(file.filename or "").name
        dest = guard(safe_path, proj, rel)
        data = await file.read(UPLOAD_CAP + 1)
        if len(data) > UPLOAD_CAP:
            raise HTTPException(413, "File is too large to upload")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return {"path": rel, "size": len(data), "kind": file_kind(rel)}

    @r.post("/projects/{name}/compile")
    async def compile_(name: str, body: CompileIn | None = None) -> dict[str, Any]:
        try:
            return await compile_project(name, body.engine if body else None)
        except LatexError as e:
            raise HTTPException(404 if str(e).startswith("No LaTeX project") else 400, str(e)) from e

    @r.get("/projects/{name}/pdf")
    def pdf(name: str) -> FileResponse:
        proj = guard(project_dir, name)
        p = proj / (Path(main_file(proj)).stem + ".pdf")
        if not p.is_file():
            raise HTTPException(404, "Not compiled yet")
        return FileResponse(p, media_type="application/pdf", headers={"Cache-Control": "no-store"})

    return r


def register_tools(api: ExtensionScope) -> None:
    async def latex_projects(ctx: dict[str, Any]) -> Any:
        return {"folder": str(root_dir()), "projects": [{k: p[k] for k in ("name", "path", "main", "engine")} for p in list_projects()]}
    api.tool(ToolSpec("latex_projects", "List the user's LaTeX projects with their folders and main files. Edit the files with the file tools.",
                      _obj({}, []), latex_projects, "latex", examples=[{}]))

    async def latex_new(ctx: dict[str, Any], name: str) -> Any:
        try:
            return create_project(name)
        except LatexError as e:
            return tool_error(str(e), field="name", example={"name": "thesis"})
    api.tool(ToolSpec("latex_new", "Create a LaTeX project with a minimal main.tex under ~/Grain/LaTeX/<name>.",
                      _obj({"name": {"type": "string"}}, ["name"]), latex_new, "latex", "writes", examples=[{"name": "thesis"}]))

    async def latex_compile(ctx: dict[str, Any], project: str, engine: str | None = None) -> Any:
        try:
            res = await compile_project(project, engine)
        except LatexError as e:
            return tool_error(str(e), field="project", alternative="latex_projects to list projects")
        res["errors"] = res["errors"][:10]
        res["warnings"] = res["warnings"][:5]
        res["log_tail"] = "" if res["ok"] else "\n".join(res["log_tail"].splitlines()[-15:])
        return res
    api.tool(ToolSpec("latex_compile", "Compile a LaTeX project. On errors, fix them by editing the files named in `errors` (file, line), then compile again.",
                      _obj({"project": {"type": "string"}, "engine": {"type": "string", "enum": list(ENGINES)}}, ["project"]),
                      latex_compile, "latex", "executes", examples=[{"project": "thesis"}]))


def activate(api: ExtensionScope) -> None:
    api.router(router())
    register_tools(api)
