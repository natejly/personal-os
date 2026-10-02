"""convert_document / render_preview / doc_guide (deliver.py). The subprocess runner and the binary lookup are
injected, so nothing real has to be installed; two end-to-end tests use the real pandoc and pdftoppm and skip when
they are absent."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import deliver, mac  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402

DESK = "d1"


def png_bytes(w: int, h: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff" * (3 * w) for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class Fake:
    """Stands in for deliver.RUNNER: records argv lists and writes the file the real program would."""

    def __init__(self, page_count: int = 5, rc: int = 0, timeout: bool = False, page_px: tuple[int, int] = (200, 100)):
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.page_count, self.rc, self.timeout, self.page_px = page_count, rc, timeout, page_px

    def __call__(self, argv: list[str], cwd: str, env: dict[str, str], timeout: float) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        self.envs.append(env)
        if self.timeout:
            raise deliver.ConvertTimeout(f"{Path(argv[0]).name} did not finish within {int(timeout)}s")
        if self.rc:
            return self.rc, "", "boom"
        name = Path(argv[0]).name
        if name == "pandoc":
            Path(argv[argv.index("-o") + 1]).write_bytes(b"converted")
        elif name == "pdftotext":
            Path(argv[-1]).write_text("text")
        elif name == "soffice":
            outdir, src = Path(argv[argv.index("--outdir") + 1]), Path(argv[-1])
            ext = argv[argv.index("--convert-to") + 1]
            (outdir / f"{src.stem}.{ext}").write_bytes(b"%PDF-fake" if ext == "pdf" else b"modern")
        elif name == "pdfinfo":
            return 0, f"Title: x\nPages:          {self.page_count}\n", ""
        elif name == "pdftoppm":
            prefix = Path(argv[-1])
            first = int(argv[argv.index("-f") + 1])
            w, h = self.page_px
            if "-scale-to" in argv:
                edge = int(argv[argv.index("-scale-to") + 1])
                w, h = (edge, round(h * edge / w)) if w >= h else (round(w * edge / h), edge)
            (prefix.parent / f"{prefix.name}-{first:02d}.png").write_bytes(png_bytes(w, h))
        return 0, "", ""


BINS = {"pandoc": "/x/pandoc", "soffice": "/x/soffice", "pdftotext": "/x/pdftotext", "pdftoppm": "/x/pdftoppm", "pdfinfo": "/x/pdfinfo"}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    class E:
        pass
    e = E()
    e.have = dict(BINS)
    monkeypatch.setattr(deliver, "which", lambda n: e.have.get(n))
    e.fake = Fake()
    monkeypatch.setattr(deliver, "RUNNER", e.fake)
    e.ws = Workspace(tmp_path / "data")
    e.root = e.ws.ensure(DESK)
    e.grant = (tmp_path / "home" / "proj").resolve()
    e.grant.mkdir(parents=True)
    monkeypatch.setattr(mac, "home", lambda: (tmp_path / "home").resolve())
    e.settings = {"workspaceRoots": [str(e.grant)]}
    e.tb = Toolbox(None, None, None, lambda: e.settings, workspace=e.ws)  # type: ignore[arg-type]
    e.ctx = {"conversation_id": "c1", "desk_id": DESK, "settings": e.settings, "tainted": False, "taint_sources": [], "message_id": None}
    e.chat = {"conversation_id": "c1", "settings": e.settings, "tainted": False, "taint_sources": [], "message_id": None}

    def call(name: str, ctx: dict[str, Any] | None = None, **args: Any) -> Any:
        return asyncio.run(e.tb.specs[name].fn(ctx or e.ctx, **args))
    e.call = call
    return e


# ---- registration and gating ----
def test_registered_in_group_deliver_with_tiers(env: Any) -> None:
    for n, danger in (("convert_document", "writes"), ("render_preview", "writes"), ("doc_guide", "safe")):
        assert (env.tb.specs[n].group, env.tb.specs[n].danger) == ("deliver", danger)
        assert env.tb.specs[n].examples


def test_availability_follows_the_binaries(env: Any) -> None:
    assert env.tb.available("convert_document") and env.tb.available("render_preview") and env.tb.available("doc_guide")
    env.have.pop("pandoc"); env.have.pop("soffice")
    assert env.tb.available("convert_document")  # pdftotext alone is enough
    env.have.pop("pdftotext"); env.have.pop("pdftoppm")
    assert not env.tb.available("convert_document") and not env.tb.available("render_preview")
    assert env.tb.available("doc_guide")


def test_which_is_cached_for_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    n = {"c": 0}

    def raw(name: str) -> str:
        n["c"] += 1
        return "/x/" + name
    monkeypatch.setattr(deliver, "_raw_which", raw)
    monkeypatch.setattr(deliver, "_WHICH_CACHE", {})
    assert deliver.which("zzz") == "/x/zzz" and deliver.which("zzz") == "/x/zzz" and n["c"] == 1
    deliver._WHICH_CACHE["zzz"] = (deliver._WHICH_CACHE["zzz"][0] - 61, "/x/zzz")
    deliver.which("zzz")
    assert n["c"] == 2


# ---- convert_document ----
def test_md_to_docx_uses_pandoc_and_returns_a_relative_path(env: Any) -> None:
    (env.root / "work" / "r.md").write_text("# hi")
    out = env.call("convert_document", path="work/r.md", to="docx")
    assert out == {"output": "work/r.docx", "bytes": 9, "converter": "pandoc"}
    argv = env.fake.calls[0]
    assert argv[:5] == ["/x/pandoc", "-f", "markdown", "-t", "docx"] and argv[-1].endswith("work/r.md")
    assert "SHELL" not in env.fake.envs[0] and env.fake.envs[0]["PATH"].startswith("/opt/homebrew/bin")


@pytest.mark.parametrize("src,to,frm,t,extra", [
    ("a.docx", "md", "docx", "markdown", ["--wrap=none"]), ("a.html", "md", "html", "markdown", ["--wrap=none"]),
    ("a.md", "html", "markdown", "html", ["-s"]), ("a.md", "odt", "markdown", "odt", []), ("a.rst", "txt", "rst", "plain", ["--wrap=none"]),
    ("a.md", "epub", "markdown", "epub", []), ("a.txt", "docx", "markdown", "docx", []),
])
def test_pandoc_pairs(env: Any, src: str, to: str, frm: str, t: str, extra: list[str]) -> None:
    (env.root / "work" / src).write_bytes(b"x")
    out = env.call("convert_document", path=f"work/{src}", to=to)
    assert out["converter"] == "pandoc", out
    argv = env.fake.calls[0]
    assert argv[1:5] == ["-f", frm, "-t", t] and all(x in argv for x in extra)


def test_markdown_alias_for_to(env: Any) -> None:
    (env.root / "work" / "a.docx").write_bytes(b"x")
    assert env.call("convert_document", path="work/a.docx", to="markdown")["output"] == "work/a.md"


@pytest.mark.parametrize("src,to,newext", [("a.docx", "pdf", "pdf"), ("a.xlsx", "pdf", "pdf"), ("a.pptx", "pdf", "pdf"),
                                           ("a.doc", "docx", "docx"), ("a.xls", "xlsx", "xlsx"), ("a.ppt", "pptx", "pptx")])
def test_soffice_conversions_use_an_isolated_profile(env: Any, src: str, to: str, newext: str) -> None:
    (env.root / "work" / src).write_bytes(b"x")
    out = env.call("convert_document", path=f"work/{src}", to=to)
    assert out["converter"] == "soffice" and out["output"] == f"work/a.{newext}", out
    argv = env.fake.calls[0]
    assert argv[0] == "/x/soffice" and "--headless" in argv and argv[argv.index("--convert-to") + 1] == newext
    assert argv[1].startswith("-env:UserInstallation=file://") and "/profile" in argv[1]
    assert (env.root / "work" / f"a.{newext}").is_file()


def test_pdf_to_txt_uses_pdftotext(env: Any) -> None:
    (env.root / "work" / "a.pdf").write_bytes(b"%PDF")
    out = env.call("convert_document", path="work/a.pdf", to="txt")
    assert out["converter"] == "pdftotext" and out["output"] == "work/a.txt"
    assert env.fake.calls[0][:2] == ["/x/pdftotext", "-layout"]


def test_never_overwrites(env: Any) -> None:
    (env.root / "work" / "r.md").write_text("x")
    (env.root / "work" / "r.docx").write_text("old")
    (env.root / "work" / "r-1.docx").write_text("old")
    assert env.call("convert_document", path="work/r.md", to="docx")["output"] == "work/r-2.docx"
    assert (env.root / "work" / "r.docx").read_text() == "old"
    out = env.call("convert_document", path="work/r.md", to="docx", output="outputs/final.docx")
    again = env.call("convert_document", path="work/r.md", to="docx", output="outputs/final.docx")
    assert out["output"] == "outputs/final.docx" and again["output"] == "outputs/final-1.docx"


def test_missing_binary_names_it_and_how_to_install(env: Any) -> None:
    (env.root / "work" / "a.docx").write_bytes(b"x")
    env.have.pop("soffice")
    out = env.call("convert_document", path="work/a.docx", to="pdf")
    assert "soffice" in out["error"] and "brew install --cask libreoffice" in out["try_instead"]
    (env.root / "work" / "a.md").write_text("x")
    env.have.pop("pandoc")
    assert "brew install pandoc" in env.call("convert_document", path="work/a.md", to="docx")["try_instead"]
    assert not env.fake.calls


def test_timeout_and_failure_are_clean_errors(env: Any) -> None:
    (env.root / "work" / "a.md").write_text("x")
    env.fake.timeout = True
    out = env.call("convert_document", path="work/a.md", to="docx")
    assert "within 120s" in out["error"] and "killed" in out["error"]
    env.fake.timeout, env.fake.rc = False, 2
    out = env.call("convert_document", path="work/a.md", to="docx")
    assert "failed (exit 2)" in out["error"] and "boom" in out["error"]
    assert not (env.root / "work" / "a.docx").exists()


def test_unsupported_pairs_say_what_is_supported(env: Any) -> None:
    (env.root / "work" / "a.pdf").write_bytes(b"x")
    (env.root / "work" / "a.md").write_text("x")
    for p, to in (("a.pdf", "docx"), ("a.md", "pdf"), ("a.md", "md")):
        out = env.call("convert_document", path=f"work/{p}", to=to)
        assert "error" in out and "Supported" in out["expected"], (p, to, out)


def test_the_real_runner_kills_the_group_on_timeout() -> None:
    with pytest.raises(deliver.ConvertTimeout):
        deliver._run_proc(["/bin/sleep", "30"], "/", {"PATH": "/bin"}, 0.3)
    rc, out, _ = deliver._run_proc(["/bin/echo", "hi"], "/", {"PATH": "/bin"}, 5)
    assert (rc, out.strip()) == (0, "hi")


# ---- paths ----
def test_paths_are_contained(env: Any) -> None:
    (env.root / "work" / "a.md").write_text("x")
    outside = env.grant.parent / "elsewhere"
    outside.mkdir()
    (outside / "a.md").write_text("x")
    for bad in ("../a.md", "work/../../a.md", "/etc/hosts", str(outside / "a.md"), "~/a.md"):
        out = env.call("convert_document", path=bad, to="docx")
        assert "error" in out, bad
    assert "error" in env.call("convert_document", path="work/a.md", to="docx", output="../escape.docx")
    # a symlink planted inside the workspace that points out is refused too
    (env.root / "work" / "link").symlink_to(outside)
    assert "error" in env.call("convert_document", path="work/link/a.md", to="docx")
    assert not env.fake.calls


def test_outside_a_desk_an_absolute_path_under_a_granted_root(env: Any) -> None:
    (env.grant / "a.md").write_text("x")
    out = env.call("convert_document", ctx=env.chat, path=str(env.grant / "a.md"), to="docx")
    assert out["output"] == str(env.grant / "a.docx"), out
    rel = env.call("convert_document", ctx=env.chat, path="a.md", to="docx")
    assert "error" in rel and "workspace" in rel["error"]
    other = env.grant.parent / "elsewhere"
    other.mkdir()
    (other / "a.md").write_text("x")
    assert "error" in env.call("convert_document", ctx=env.chat, path=str(other / "a.md"), to="docx")
    assert "error" in env.call("convert_document", ctx=env.chat, path=str(env.grant / "a.md"), to="docx", output=str(other / "o.docx"))


# ---- render_preview ----
def test_parse_pages() -> None:
    assert deliver.parse_pages(None, None) == ([1, 2, 3], "")
    assert deliver.parse_pages("2", None)[0] == [2]
    assert deliver.parse_pages("1,4-5", None)[0] == [1, 4, 5]
    assert deliver.parse_pages("1-3", 2) == ([1, 2], "the file has 2 page(s); the rest of the requested range was dropped")
    pages, note = deliver.parse_pages("1-20", None)
    assert pages == list(range(1, 9)) and "at most 8" in note
    for bad in ("x", "0", "3-1", "1-"):
        with pytest.raises(ValueError):
            deliver.parse_pages(bad, None)


def test_render_pdf_builds_pdftoppm_args_and_names_files(env: Any) -> None:
    (env.root / "outputs").mkdir(exist_ok=True)
    (env.root / "outputs" / "deck.pdf").write_bytes(b"%PDF")
    out = env.call("render_preview", path="outputs/deck.pdf", pages="2-3", dpi=999)
    assert [p["page"] for p in out["pages"]] == [2, 3] and out["total_pages"] == 5
    assert out["pages"][0]["path"] == "work/previews/deck-p2.png" and (env.root / "work/previews/deck-p3.png").is_file()
    assert (out["pages"][0]["width"], out["pages"][0]["height"]) == (200, 100)
    assert "view_image" in out["note"]
    ppm = [c for c in env.fake.calls if c[0] == "/x/pdftoppm"][0]
    assert ppm[1:5] == ["-png", "-r", "150", "-f"] and ppm[5] == "2" and ppm[7] == "2"  # dpi capped at 150


def test_render_defaults_and_page_cap(env: Any) -> None:
    (env.root / "work" / "a.pdf").write_bytes(b"%PDF")
    env.fake.page_count = 30
    out = env.call("render_preview", path="work/a.pdf")
    assert [p["page"] for p in out["pages"]] == [1, 2, 3]
    assert [c for c in env.fake.calls if c[0] == "/x/pdftoppm"][0][3] == "80"
    out = env.call("render_preview", path="work/a.pdf", pages="1-20")
    assert len(out["pages"]) == 8 and "at most 8" in out["note"]
    out = env.call("render_preview", path="work/a.pdf", pages="40")
    assert "error" in out and "30 page" in out["error"]
    assert "error" in env.call("render_preview", path="work/a.pdf", pages="abc")


def test_long_edge_is_capped(env: Any) -> None:
    (env.root / "work" / "a.pdf").write_bytes(b"%PDF")
    env.fake.page_px = (2400, 1200)
    out = env.call("render_preview", path="work/a.pdf", pages="1")
    assert out["pages"][0]["width"] == 1568 and out["pages"][0]["height"] == 784
    assert any("-scale-to" in c for c in env.fake.calls)


def test_office_file_goes_through_soffice_then_pdftoppm(env: Any) -> None:
    (env.root / "outputs").mkdir(exist_ok=True)
    (env.root / "outputs" / "r.docx").write_bytes(b"x")
    out = env.call("render_preview", path="outputs/r.docx", pages="1")
    assert out["pages"][0]["path"] == "work/previews/r-p1.png"
    names = [Path(c[0]).name for c in env.fake.calls]
    assert names.index("soffice") < names.index("pdftoppm")


def test_soffice_missing_is_said_plainly(env: Any) -> None:
    (env.root / "work" / "r.pptx").write_bytes(b"x")
    env.have.pop("soffice")
    out = env.call("render_preview", path="work/r.pptx")
    assert out["pages"] == [] and "soffice" in out["note"] and "cannot be checked visually" in out["note"]
    assert "convert_document" in out["note"] and "run_python" in out["note"]
    (env.root / "work" / "r.pdf").write_bytes(b"%PDF")  # a pdf still previews
    assert env.call("render_preview", path="work/r.pdf", pages="1")["pages"]


def test_image_is_returned_as_is_and_html_is_out_of_scope(env: Any) -> None:
    (env.root / "work" / "c.png").write_bytes(png_bytes(30, 20))
    out = env.call("render_preview", path="work/c.png")
    assert out["pages"] == [{"page": 1, "path": "work/c.png", "width": 30, "height": 20}]
    assert not env.fake.calls
    (env.root / "work" / "p.html").write_text("<p>x")
    assert "browser" in env.call("render_preview", path="work/p.html")["try_instead"]
    (env.root / "work" / "z.zip").write_bytes(b"x")
    assert "error" in env.call("render_preview", path="work/z.zip")


def test_render_outside_a_desk_uses_a_temp_dir(env: Any) -> None:
    (env.grant / "a.pdf").write_bytes(b"%PDF")
    out = env.call("render_preview", ctx=env.chat, path=str(env.grant / "a.pdf"), pages="1")
    p = Path(out["pages"][0]["path"])
    assert p.is_absolute() and p.is_file() and env.grant not in p.parents
    shutil.rmtree(p.parent, ignore_errors=True)


# ---- doc_guide ----
BANNED = ("excel", "powerpoint", "microsoft", "google", "canva", "notion", "copilot", "chatgpt", "openai", "numbers.app", "keynote")


@pytest.mark.parametrize("fmt", deliver.GUIDE_FORMATS)
def test_every_guide_is_sound(env: Any, fmt: str) -> None:
    out = env.call("doc_guide", format=fmt)
    g = out["guide"]
    assert 800 < len(g) <= deliver.GUIDE_MAX_CHARS, len(g)
    low = g.lower()
    assert "check your work" in low and "render_preview" in low or fmt == "csv"
    assert "```python" in g and "outputs/" in g
    assert not [b for b in BANNED if re.search(rf"\b{re.escape(b)}\b", low.replace("to_excel", ""))]
    if fmt != "csv" and fmt != "charts":
        assert "python_install" in g


def test_unknown_format_lists_the_options(env: Any) -> None:
    out = env.call("doc_guide", format="rtf")
    assert "error" in out and all(f in out["expected"] for f in deliver.GUIDE_FORMATS)
    assert env.call("doc_guide", format=".DOCX")["format"] == "docx"


# ---- real binaries ----
@pytest.mark.skipif(not (shutil.which("pandoc", path="/opt/homebrew/bin:/usr/local/bin:/usr/bin")), reason="pandoc not installed")
def test_real_pandoc_md_to_docx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deliver, "_WHICH_CACHE", {})
    ws = Workspace(tmp_path / "data")
    root = ws.ensure(DESK)
    (root / "work" / "r.md").write_text("# Title\n\nHello **world**.\n")
    tb = Toolbox(None, None, None, lambda: {}, workspace=ws)  # type: ignore[arg-type]
    ctx = {"conversation_id": "c", "desk_id": DESK, "settings": {}, "tainted": False, "taint_sources": [], "message_id": None}
    out = asyncio.run(tb.specs["convert_document"].fn(ctx, path="work/r.md", to="docx"))
    assert out["converter"] == "pandoc" and out["output"] == "work/r.docx", out
    data = (root / "work" / "r.docx").read_bytes()
    assert data[:2] == b"PK" and out["bytes"] == len(data)
    # and back again
    back = asyncio.run(tb.specs["convert_document"].fn(ctx, path="work/r.docx", to="md"))
    assert "Hello **world**" in (root / back["output"]).read_text()


@pytest.mark.skipif(not (shutil.which("pdftoppm", path="/opt/homebrew/bin:/usr/local/bin:/usr/bin")), reason="pdftoppm not installed")
def test_real_pdftoppm_renders_a_tiny_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pypdf = pytest.importorskip("pypdf")
    monkeypatch.setattr(deliver, "_WHICH_CACHE", {})
    ws = Workspace(tmp_path / "data")
    root = ws.ensure(DESK)
    w = pypdf.PdfWriter()
    for _ in range(2):
        w.add_blank_page(width=300, height=200)
    with (root / "outputs" / "t.pdf").open("wb") as fh:
        w.write(fh)
    tb = Toolbox(None, None, None, lambda: {}, workspace=ws)  # type: ignore[arg-type]
    ctx = {"conversation_id": "c", "desk_id": DESK, "settings": {}, "tainted": False, "taint_sources": [], "message_id": None}
    out = asyncio.run(tb.specs["render_preview"].fn(ctx, path="outputs/t.pdf", pages="1-5", dpi=72))
    assert out["total_pages"] == 2 and [p["page"] for p in out["pages"]] == [1, 2], out
    assert (out["pages"][0]["width"], out["pages"][0]["height"]) == (300, 200)
    assert (root / "work" / "previews" / "t-p2.png").read_bytes()[:4] == b"\x89PNG"
