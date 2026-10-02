"""Deliverables: convert_document, render_preview and doc_guide (group `deliver`).

A desk can already write files with run_python and the shell; what it lacks is a way to change a document from one
format to another, a way to SEE what it made, and the know-how to make an office file that holds up. These three
tools cover that. The converters are whatever is installed (pandoc, LibreOffice's soffice, poppler's pdftotext and
pdftoppm); each tool is only offered when a binary it can use exists, and a missing one is named in the error with
how to install it.

Everything is path-contained the way the file tools are: inside a desk, paths are relative to the desk workspace and
resolved through Workspace.resolve_in; outside one, an absolute path must sit under a granted workspace root
(fsx.grants_for). Output is only ever written inside one of those. The external programs run with no shell, a
scrubbed environment, a throwaway LibreOffice profile and a hard timeout that kills the whole process group.

The subprocess runner and the binary lookup are module attributes (RUNNER, which) so the tests can inject both.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import struct
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from . import fsx, shell
from .workspace import WorkspaceError

TIMEOUT_S = 120
WHICH_TTL_S = 60.0
GUIDES_DIR = Path(__file__).parent / "guides"
GUIDE_FORMATS = ("docx", "xlsx", "pptx", "pdf", "charts", "csv")
GUIDE_MAX_CHARS = 4500

DEFAULT_PAGES = 3
MAX_PAGES = 8
DEFAULT_DPI, MIN_DPI, MAX_DPI = 80, 20, 150
MAX_EDGE_PX = 1568
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
OFFICE_EXTS = (".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp")
LEGACY = {".doc": "docx", ".xls": "xlsx", ".ppt": "pptx"}
# pandoc speaks these; "txt" reads as markdown and writes plain text. Same-to-same is not a conversion.
PANDOC_FORMATS = {"md": "markdown", "html": "html", "docx": "docx", "odt": "odt", "rst": "rst", "txt": "plain", "epub": "epub"}
PANDOC_READ = {**PANDOC_FORMATS, "txt": "markdown"}
EXT_ALIASES = {"markdown": "md", "htm": "html", "text": "txt"}
INSTALL = {"pandoc": "brew install pandoc", "soffice": "brew install --cask libreoffice", "pdftotext": "brew install poppler",
           "pdftoppm": "brew install poppler"}
FILE_HOME = ("Files live in the desk workspace (paths relative to it, e.g. outputs/report.docx) or, outside a desk, "
             "under a workspace root the user granted in Settings.")


class ConvertTimeout(Exception):
    pass


def _run_proc(argv: list[str], cwd: str, env: dict[str, str], timeout: float) -> tuple[int, str, str]:
    """No shell, own process group, and on timeout the whole group dies (soffice forks helpers)."""
    p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            p.kill()
        p.communicate()
        raise ConvertTimeout(f"{Path(argv[0]).name} did not finish within {int(timeout)}s") from None
    return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


RUNNER: Callable[[list[str], str, dict[str, str], float], tuple[int, str, str]] = _run_proc
_WHICH_CACHE: dict[str, tuple[float, str | None]] = {}


def _search_path() -> str:
    # The backend is launched by the app, whose PATH often lacks Homebrew; the converters are run with SAFE_PATH anyway.
    return shell.SAFE_PATH + os.pathsep + os.environ.get("PATH", "")


def _raw_which(name: str) -> str | None:
    return shutil.which(name, path=_search_path())


def which(name: str) -> str | None:
    """Binary lookup cached for WHICH_TTL_S: availability is asked on every turn's tool list."""
    now = time.monotonic()
    hit = _WHICH_CACHE.get(name)
    if hit and now - hit[0] < WHICH_TTL_S:
        return hit[1]
    found = _raw_which(name)
    _WHICH_CACHE[name] = (now, found)
    return found


def _missing(binary: str, what: str) -> dict[str, Any]:
    from .tools import tool_error
    return tool_error(f"{what} needs `{binary}`, which is not installed on this Mac.",
                      alternative=f"ask the user to run `{INSTALL[binary]}`, or produce the file with run_python instead")


# ---- paths ----
def _desk_id(ctx: dict[str, Any], tb: Any) -> str | None:
    did = ctx.get("desk_id")
    return str(did) if did and getattr(tb, "workspace", None) is not None else None


def _resolve(tb: Any, ctx: dict[str, Any], raw: Any) -> tuple[Path | None, dict[str, Any] | None]:
    """A real, contained path (resolved through symlinks), or an error envelope. Sources and outputs go through here."""
    from .tools import tool_error
    s = str(raw or "").strip()
    if not s:
        return None, tool_error("A path is required.", field="path", example={"path": "outputs/report.docx"})
    did = _desk_id(ctx, tb)
    if did and not os.path.isabs(os.path.expanduser(s)):
        try:
            return tb.workspace.resolve_in(did, s), None
        except WorkspaceError as e:
            return None, tool_error(str(e), field="path", expected="a path inside the desk workspace such as outputs/report.docx")
    g = fsx.grants_for(tb, ctx)
    p = Path(os.path.realpath(os.path.expanduser(s)))
    if not os.path.isabs(os.path.expanduser(s)):
        return None, tool_error(f"{s!r} is relative and there is no desk workspace to resolve it against. " + FILE_HOME, field="path")
    if not (g.in_desk(p) or g.in_roots(p)):
        return None, tool_error(f"{s} is outside every folder you may use. " + FILE_HOME, field="path")
    return p, None


def _shown(tb: Any, ctx: dict[str, Any], p: Path) -> str:
    """Relative to the desk workspace in a desk, absolute otherwise: what the model passes back to other tools."""
    did = _desk_id(ctx, tb)
    if did:
        try:
            return p.relative_to(tb.workspace.desk_root(did).resolve()).as_posix()
        except (ValueError, WorkspaceError):
            pass
    return str(p)


def _unique(p: Path) -> Path:
    """Never overwrite: report.pdf, report-1.pdf, report-2.pdf ..."""
    if not p.exists():
        return p
    n = 1
    while True:
        q = p.with_name(f"{p.stem}-{n}{p.suffix}")
        if not q.exists():
            return q
        n += 1


def _ext(p: Path | str) -> str:
    e = Path(str(p)).suffix.lower().lstrip(".")
    return EXT_ALIASES.get(e, e)


def _env(tmp: str) -> dict[str, str]:
    return shell.scrubbed_env(tmp)


def _soffice_argv(binary: str, profile: str, outdir: str, target: str, src: Path) -> list[str]:
    return [binary, f"-env:UserInstallation=file://{profile}", "--headless", "--norestore", "--convert-to", target, "--outdir", outdir, str(src)]


def _tail(text: str, n: int = 600) -> str:
    return text.strip()[-n:]


# ---- convert_document ----
def _plan(src: Path, to: str) -> tuple[str, str] | str:
    """(converter, target extension) for this pair, or a sentence on why there is none."""
    se = _ext(src)
    sx = src.suffix.lower()
    if sx in LEGACY:
        if to in (LEGACY[sx], "pdf"):
            return "soffice", to
        return f"{sx} files convert to .{LEGACY[sx]} or pdf; to={to!r} is not available for them."
    if se in ("docx", "xlsx", "pptx", "odt", "ods", "odp") and to == "pdf":
        return "soffice", "pdf"
    if se == "pdf" and to == "txt":
        return "pdftotext", "txt"
    if se in PANDOC_READ and to in PANDOC_FORMATS and se != to:
        return "pandoc", to
    if se == "pdf":
        return "pdf files convert to txt only (and nothing converts to pdf except office files)."
    return f"no converter for {se or 'this file'} -> {to}."


def _supported_text() -> str:
    return ("Supported: markdown/html/docx/odt/rst/txt/epub between each other (pandoc); docx/xlsx/pptx/odt/ods/odp -> pdf and "
            ".doc/.xls/.ppt -> .docx/.xlsx/.pptx (LibreOffice); pdf -> txt (pdftotext).")


def _pandoc_argv(binary: str, src: Path, to: str, out: Path) -> list[str]:
    a = [binary, "-f", PANDOC_READ[_ext(src)], "-t", PANDOC_FORMATS[to], "-o", str(out)]
    if to in ("md", "txt", "rst"):
        a.append("--wrap=none")
    if to == "html":
        a.append("-s")
    if _ext(src) in ("docx", "odt", "epub") and to in ("md", "txt", "rst"):
        # A word-processor Title is document metadata to pandoc: without --standalone it is dropped from text output, and the
        # converted file loses its own heading. Embedded pictures are written next to the output instead of left as dangling
        # `media/imageN.png` references (pandoc only creates the folder when there is something to extract).
        a.append("-s")
        if to != "txt":
            a.append(f"--extract-media={os.path.relpath(str(out.with_suffix('')) + '_media', str(src.parent))}")  # pandoc runs in src.parent
    return a + [str(src)]


def _convert_sync(src: Path, to: str, out: Path) -> dict[str, Any]:
    from .tools import tool_error
    plan = _plan(src, to)
    if isinstance(plan, str):
        return tool_error(plan, field="to", expected=_supported_text())
    conv, target = plan
    binary = which(conv)
    if not binary:
        return _missing(conv, f"Converting {src.suffix or 'this file'} to {to}")
    tmp = tempfile.mkdtemp(prefix="deliver-")
    try:
        env = _env(tmp)
        if conv == "pandoc":
            argv, produced = _pandoc_argv(binary, src, target, out), out
        elif conv == "pdftotext":
            argv, produced = [binary, "-layout", str(src), str(out)], out
        else:
            outdir = os.path.join(tmp, "out")
            os.makedirs(outdir)
            argv = _soffice_argv(binary, os.path.join(tmp, "profile"), outdir, target, src)
            produced = Path(outdir) / f"{src.stem}.{target}"
        try:
            rc, so, se_ = RUNNER(argv, str(src.parent), env, TIMEOUT_S)
        except ConvertTimeout as e:
            return tool_error(f"{e}. The process was killed.", alternative="try a smaller file, or convert it with run_python")
        except OSError as e:
            return tool_error(f"Could not run {conv}: {e}")
        if rc != 0 or not produced.is_file():
            return tool_error(f"{conv} failed (exit {rc}): {_tail(se_ or so) or 'no output'}")
        if produced != out:
            shutil.move(str(produced), str(out))
        return {"converter": conv, "bytes": out.stat().st_size}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _any_converter() -> bool:
    return any(which(b) for b in ("pandoc", "soffice", "pdftotext"))


# ---- render_preview ----
def parse_pages(spec: Any, total: int | None) -> tuple[list[int], str]:
    """'1-3', '2', '1,4-5' -> 1-based page numbers (ascending, de-duplicated, at most MAX_PAGES) and a note on any clipping."""
    notes: list[str] = []
    if spec is None or str(spec).strip() == "":
        pages = list(range(1, DEFAULT_PAGES + 1))
    else:
        pages = []
        for part in re.split(r"[,\s]+", str(spec).strip()):
            if not part:
                continue
            m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
            if not m:
                raise ValueError(f"cannot read {part!r} in pages={spec!r}; use forms like 2, 1-3 or 1,4-5")
            a = int(m.group(1)); b = int(m.group(2) or a)
            if a < 1 or b < a:
                raise ValueError(f"pages={spec!r} has a bad range {part!r}; pages count from 1 and ranges run upwards")
            pages.extend(range(a, min(b, a + 4 * MAX_PAGES) + 1))
    pages = sorted(set(pages))
    if total is not None:
        kept = [p for p in pages if p <= total]
        if len(kept) < len(pages):
            notes.append(f"the file has {total} page(s); the rest of the requested range was dropped")
        pages = kept
    if len(pages) > MAX_PAGES:
        pages = pages[:MAX_PAGES]
        notes.append(f"at most {MAX_PAGES} pages per call; ask for the next range for more")
    return pages, "; ".join(notes)


def _png_size(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as fh:
            head = fh.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", head[16:24])
            return int(w), int(h)
        from PIL import Image  # other formats: Pillow when the backend has it
        with Image.open(path) as im:
            return im.size
    except Exception:  # noqa: BLE001 - a size is a nicety, never a reason to fail
        return None


def _page_count(pdf: Path, env: dict[str, str]) -> int | None:
    info = which("pdfinfo")
    if info:
        try:
            rc, out, _ = RUNNER([info, str(pdf)], str(pdf.parent), env, 30)
            m = re.search(r"^Pages:\s+(\d+)", out, re.M)
            if rc == 0 and m:
                return int(m.group(1))
        except (ConvertTimeout, OSError):
            pass
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(pdf)).pages)
    except Exception:  # noqa: BLE001
        return None


def _render_sync(src: Path, pages_spec: Any, dpi: int, outdir: Path, stem: str) -> dict[str, Any]:
    """Returns {"pages": [(n, Path)], "total": int|None, "extra": str} or an error envelope."""
    from .tools import tool_error
    ppm = which("pdftoppm")
    if not ppm:
        return _missing("pdftoppm", "Rendering a page image")
    tmp = tempfile.mkdtemp(prefix="deliver-")
    try:
        env = _env(tmp)
        pdf = src
        if _ext(src) != "pdf":
            soffice = which("soffice")
            if not soffice:
                return {"pages": [], "total": None, "soffice_missing": True}
            conv = Path(tmp) / "conv"
            conv.mkdir()
            try:
                rc, so, se_ = RUNNER(_soffice_argv(soffice, os.path.join(tmp, "profile"), str(conv), "pdf", src), str(src.parent), env, TIMEOUT_S)
            except ConvertTimeout as e:
                return tool_error(f"{e}. The process was killed.", alternative="try a smaller file")
            pdf = conv / f"{src.stem}.pdf"
            if rc != 0 or not pdf.is_file():
                return tool_error(f"soffice could not turn {src.name} into a PDF (exit {rc}): {_tail(se_ or so) or 'no output'}")
        total = _page_count(pdf, env)
        try:
            pages, note = parse_pages(pages_spec, total)
        except ValueError as e:
            return tool_error(str(e), field="pages", example={"pages": "1-3"})
        if not pages:
            return tool_error(f"The file has {total} page(s); none of the requested pages exist.", field="pages")
        outdir.mkdir(parents=True, exist_ok=True)
        done: list[tuple[int, Path]] = []
        for n in pages:
            shots = Path(tmp) / f"p{n}"
            shots.mkdir()
            argv = [ppm, "-png", "-r", str(dpi), "-f", str(n), "-l", str(n), str(pdf), str(shots / "pg")]
            try:
                rc, so, se_ = RUNNER(argv, tmp, env, TIMEOUT_S)
            except ConvertTimeout as e:
                return tool_error(f"{e}. The process was killed.")
            got = sorted(shots.glob("pg*.png"))
            if rc != 0 or not got:
                return tool_error(f"pdftoppm failed on page {n} (exit {rc}): {_tail(se_ or so) or 'no output'}")
            size = _png_size(got[0])
            if size and max(size) > MAX_EDGE_PX:  # re-render so the long edge is exactly the cap; never upscale a small page
                rc, so, se_ = RUNNER([ppm, "-png", "-scale-to", str(MAX_EDGE_PX), "-f", str(n), "-l", str(n), str(pdf),
                                      str(shots / "cap")], tmp, env, TIMEOUT_S)
                capped = sorted(shots.glob("cap*.png"))
                if rc == 0 and capped:
                    got = capped
            dest = outdir / f"{stem}-p{n}.png"
            shutil.move(str(got[0]), str(dest))
            done.append((n, dest))
        return {"pages": done, "total": total, "extra": note}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _intp(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ---- doc_guide ----
def _guide_key(fmt: Any) -> str:
    f = str(fmt or "").strip().lower().lstrip(".")
    f = {"doc": "docx", "spreadsheet": "xlsx", "slides": "pptx", "chart": "charts", "plot": "charts", "plots": "charts"}.get(f, f)
    return f


def load_guide(fmt: str) -> str | None:
    f = _guide_key(fmt)
    if f not in GUIDE_FORMATS:
        return None
    try:
        return (GUIDES_DIR / f"{f}.md").read_text(encoding="utf-8")
    except OSError:
        return None


def register(tb: Any) -> None:
    """Register convert_document, render_preview and doc_guide on the Toolbox (group `deliver`)."""
    from .tools import ToolSpec, _obj, tool_error
    R = tb.specs.__setitem__

    async def convert_document(ctx: dict[str, Any], path: str, to: str, output: str | None = None) -> Any:
        src, err = _resolve(tb, ctx, path)
        if err:
            return err
        assert src is not None
        if not src.is_file():
            return tool_error(f"{path} is not a file here.", field="path", alternative="list the folder first (desk_list_files)")
        why = fsx.sensitive_reason(path, src)
        if why:
            return tool_error(f"{path} cannot be read: {why}.", field="path")
        target = EXT_ALIASES.get(str(to or "").strip().lower().lstrip("."), str(to or "").strip().lower().lstrip("."))
        if not target:
            return tool_error("to is required: the format to convert into.", field="to", expected=_supported_text())
        if output:
            out, err = _resolve(tb, ctx, output)
            if err:
                return err
            assert out is not None
            out = _unique(out)
            out.parent.mkdir(parents=True, exist_ok=True)
        else:
            out = _unique(src.with_suffix("." + target))
        res = await asyncio.to_thread(_convert_sync, src, target, out)
        if "error" in res:
            return res
        return {"output": _shown(tb, ctx, out), "bytes": res["bytes"], "converter": res["converter"]}
    spec = ToolSpec("convert_document", "Convert a document from one format to another: markdown/html/docx/odt/rst/txt/epub between each "
                    "other (pandoc), word-processor, spreadsheet, slide and OpenDocument files to pdf and old .doc/.xls/.ppt to the modern "
                    "format (LibreOffice), pdf to txt (pdftotext). Use it when the user wants a file in another format or you wrote "
                    "markdown and need a .docx. The output goes next to the source (never overwriting: report-1.pdf) unless you give "
                    "`output`. " + FILE_HOME,
                    _obj({"path": {"type": "string", "description": "The file to convert"},
                          "to": {"type": "string", "description": "Target format: docx, pdf, md, html, txt, odt, rst, epub, xlsx, pptx"},
                          "output": {"type": "string", "description": "Where to write it (optional)"}}, ["path", "to"]),
                    convert_document, "deliver", "writes",
                    examples=[{"path": "work/report.md", "to": "docx"}, {"path": "outputs/deck.pptx", "to": "pdf"},
                              {"path": "work/notes.docx", "to": "md", "output": "work/notes-clean.md"}])
    spec.available_fn = _any_converter
    R("convert_document", spec)

    async def render_preview(ctx: dict[str, Any], path: str, pages: str | None = None, dpi: int | None = None) -> Any:
        src, err = _resolve(tb, ctx, path)
        if err:
            return err
        assert src is not None
        if not src.is_file():
            return tool_error(f"{path} is not a file here.", field="path", alternative="list the folder first (desk_list_files)")
        why = fsx.sensitive_reason(path, src)
        if why:
            return tool_error(f"{path} cannot be read: {why}.", field="path")
        ext = src.suffix.lower()
        look = "Call view_image on a page to check the layout (clipped text, overflow, overlaps), fix the source, and re-render."
        if ext in IMAGE_EXTS:
            size = _png_size(src)
            return {"pages": [{"page": 1, "path": _shown(tb, ctx, src), "width": size[0] if size else None,
                               "height": size[1] if size else None}], "total_pages": 1,
                    "note": "That file is already an image; call view_image on it directly."}
        if ext in (".html", ".htm"):
            return tool_error("render_preview does not draw web pages.", alternative="open it in the browser and take a screenshot")
        if ext != ".pdf" and ext not in OFFICE_EXTS and ext not in LEGACY:
            return tool_error(f"render_preview takes a pdf, an office file (docx/xlsx/pptx/odt/ods/odp) or an image, not {ext or 'this'}.",
                              field="path")
        dpi_n = max(MIN_DPI, min(MAX_DPI, _intp(dpi, DEFAULT_DPI)))
        did = _desk_id(ctx, tb)
        if did:
            outdir = tb.workspace.ensure(did) / "work" / "previews"
        else:
            outdir = Path(tempfile.mkdtemp(prefix="grain-preview-"))
        res = await asyncio.to_thread(_render_sync, src, pages, dpi_n, outdir, src.stem)
        if "error" in res:
            return res
        if res.get("soffice_missing"):
            return {"pages": [], "total_pages": None,
                    "note": f"Cannot preview {src.name}: turning an office file into pages needs LibreOffice (soffice), which is not installed. "
                            "convert_document or run_python can still produce the file, but its layout cannot be checked visually here."
                            " Say so to the user rather than claiming it looks right. A PDF can be previewed."}
        out_pages = []
        for n, p in res["pages"]:
            size = _png_size(p)
            out_pages.append({"page": n, "path": _shown(tb, ctx, p), "width": size[0] if size else None, "height": size[1] if size else None})
        note = look + (f" ({res['extra']})" if res.get("extra") else "")
        return {"pages": out_pages, "total_pages": res["total"], "note": note}
    spec = ToolSpec("render_preview", "Render pages of a PDF or office file (docx/xlsx/pptx/odt/ods/odp) to PNG images so you can look at them "
                    "with view_image. Use it after producing a document, deck or spreadsheet to check the layout before you hand it over. "
                    "`pages` like \"1-3\" (default the first 3, at most 8 per call), `dpi` default 80 (max 150). Images land in "
                    "work/previews/. Web pages are out of scope: use the browser's screenshot.",
                    _obj({"path": {"type": "string"}, "pages": {"type": "string", "description": "e.g. 1-3 or 1,4"},
                          "dpi": {"type": "integer", "default": DEFAULT_DPI}}, ["path"]),
                    render_preview, "deliver", "writes",
                    examples=[{"path": "outputs/report.docx"}, {"path": "outputs/deck.pptx", "pages": "1-6", "dpi": 60}])
    spec.available_fn = lambda: bool(which("pdftoppm"))
    R("render_preview", spec)

    async def doc_guide(ctx: dict[str, Any], format: str) -> Any:
        text = load_guide(format)
        if text is None:
            return tool_error(f"No guide for {format!r}.", field="format", expected=" | ".join(GUIDE_FORMATS),
                              example={"format": "docx"})
        return {"format": _guide_key(format), "guide": text}
    R("doc_guide", ToolSpec("doc_guide", "A practical guide to producing one kind of deliverable with the Python libraries available to a "
                            "desk: which library to use, a working skeleton, the gotchas, and how to check your output. Read it BEFORE "
                            "you write a .docx, .xlsx, .pptx, .pdf, a chart or a CSV.",
                            _obj({"format": {"type": "string", "enum": list(GUIDE_FORMATS)}}, ["format"]),
                            doc_guide, "deliver", "safe", examples=[{"format": "xlsx"}, {"format": "pptx"}]))
