"""LaTeX extension: log parser, projects as Files folders, assets, markdown helpers, and real latexmk compiles
(skipped when TeX is missing). Run: cd backend && PYTHONPATH=. .venv/bin/python tests/test_latex.py"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from personal_os.db import Database
from personal_os.docs import Docs, is_latex_source
from personal_os.extensions.latex import extension as lx

HAVE_TEX = lx.shutil.which("latexmk", path=lx.tex_env()["PATH"]) is not None
MIGRATION = Path(lx.__file__).parent / "migrations" / "0001_projects.sql"
BROKEN = "\\documentclass{article}\n\\begin{document}\nhello\n\\nonsense\n\\end{document}\n"


class ParseLogTests(unittest.TestCase):
    def test_file_line_error(self) -> None:
        r = lx.parse_log("./chap/one.tex:12: Undefined control sequence.\nl.12 \\foo\n", "main.tex")
        self.assertEqual(r["errors"], [{"file": "chap/one.tex", "line": 12, "message": "Undefined control sequence."}])

    def test_bang_block_takes_line_from_l_line(self) -> None:
        r = lx.parse_log("! LaTeX Error: File `x.sty' not found.\n\nType X to quit.\nl.3 \\usepackage{x}\n", "main.tex")
        self.assertEqual(r["errors"], [{"file": "main.tex", "line": 3, "message": "LaTeX Error: File `x.sty' not found."}])

    def test_emergency_stop_is_not_an_error(self) -> None:
        self.assertEqual(lx.parse_log("! Emergency stop.\n<*> main.tex\n")["errors"], [])

    def test_warnings_and_boxes(self) -> None:
        log = ("LaTeX Warning: Reference `a' on page 1 undefined on input line 7.\n"
               "LaTeX Warning: Label `b' multiply\n defined on input line 9.\n"
               "Overfull \\hbox (3.0pt too wide) in paragraph at lines 20--22\n")
        self.assertEqual([x["line"] for x in lx.parse_log(log)["warnings"]], [7, 9, 20])

    def test_duplicates_and_cap(self) -> None:
        log = "a.tex:1: Boom.\n" * 3 + "".join(f"Overfull \\hbox (1pt) in paragraph at lines {i}--{i}\n" for i in range(80))
        r = lx.parse_log(log)
        self.assertEqual(len(r["errors"]), 1)
        self.assertEqual(len(r["warnings"]), lx.MAX_DIAGNOSTICS)


class ProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(Path(self.tmp.name) / "data")
        with self.db.tx() as c:
            c.executescript(MIGRATION.read_text())
            c.execute("INSERT INTO projects(id, name, created_at) VALUES('p1', 'Thesis', 0)")
        self.docs = Docs(self.db)
        self.names = {"p1": "Thesis"}
        self.lx = lx.Latex(self.docs, self.db, Path(self.tmp.name) / "ext", lambda: self.names)

    def doc(self, title: str, content: str = "x", folder: str = "", project: str | None = None) -> dict:
        return self.docs.create(title, content, project, folder)

    def test_create_makes_a_folder_with_main_tex(self) -> None:
        p = self.lx.create("thesis", "Thesis")  # a Grain project by name
        self.assertEqual((p["folder"], p["main"], p["project_id"], p["project"]), ("thesis", "main.tex", "p1", "Thesis"))
        self.assertIn("\\documentclass{article}", self.docs.get(p["main_doc_id"])["content"])
        self.assertEqual(self.docs.get(p["main_doc_id"])["project_id"], "p1")
        self.assertEqual(self.lx.create("thesis", "p1")["folder"], "thesis 2")  # a unique folder
        self.assertEqual(self.lx.create("loose")["project_id"], None)
        self.assertEqual([x["folder"] for x in self.lx.list_projects("Thesis")], ["thesis", "thesis 2"])
        self.assertEqual(len(self.lx.list_projects()), 3)
        for bad in ("a/b", "", ".."):
            with self.assertRaises(lx.LatexError, msg=bad):
                self.lx.create(bad)
        with self.assertRaises(lx.LatexError):
            self.lx.create("x", "nope")  # an unknown Grain project

    def test_main_is_main_tex_or_the_one_documentclass(self) -> None:
        a = self.doc("paper.tex", "\\documentclass{article}", "A")
        self.doc("intro.tex", "no class here", "A")
        self.assertEqual(self.lx.project_for(a["id"])["main"], "paper.tex")
        self.doc("other.tex", "\\documentclass{book}", "A")  # two candidates: no project, and a .tex alone is its own
        self.assertEqual(self.lx.project_for(a["id"])["main_doc_id"], a["id"])

    def test_subfolder_files_resolve_to_the_project_and_get_relative_paths(self) -> None:
        p = self.lx.create("book")
        ch = self.doc("one.tex", "chapter", "book/chapters")
        bib = self.doc("refs.bib", "@book{}", "book")
        self.assertEqual(self.lx.project_for(ch["id"])["folder"], "book")
        self.assertEqual({f["path"] for f in self.lx.context(ch["id"])["files"]}, {"main.tex", "chapters/one.tex", "refs.bib"})
        self.assertEqual(self.lx.project_for(bib["id"])["main_doc_id"], p["main_doc_id"])
        with self.assertRaises(lx.LatexError):
            self.lx.project_for(self.doc("note.md")["id"])
        with self.assertRaises(lx.LatexError):
            self.lx.project_for(self.doc("x.bib", "", "empty")["id"])

    def test_materialize_writes_docs_and_assets_with_relative_paths(self) -> None:
        p = self.lx.create("book")
        self.doc("one.tex", "chapter", "book/chapters")
        self.doc("elsewhere.tex", "no", "other")
        self.lx.put_asset("", "book", "img/fig.png", b"png")
        build, ids = self.lx.materialize(p)
        self.assertEqual(sorted(ids), ["chapters/one.tex", "main.tex"])
        self.assertEqual((build / "chapters" / "one.tex").read_text(), "chapter")
        self.assertEqual((build / "img" / "fig.png").read_bytes(), b"png")
        self.assertFalse((build / "elsewhere.tex").exists())
        self.docs.delete(ids["chapters/one.tex"])
        self.lx.materialize(p)
        self.assertFalse((build / "chapters" / "one.tex").exists())  # a deleted doc leaves the build folder

    def test_titles_that_escape_or_look_like_options_are_refused(self) -> None:
        p = self.lx.create("book")
        for bad in ("../evil.tex", "a/b.tex", "-x.tex", ".hid.tex", "a\\b.tex"):
            d = self.doc(bad, "x", "book")
            with self.assertRaises(lx.LatexError, msg=bad):
                self.lx.materialize(p)
            self.docs.delete(d["id"])
        self.doc("fine.tex", "x", "book")
        self.lx.materialize(p)
        for bad in ("-main.tex", "../main.tex"):
            with self.assertRaises(lx.LatexError):
                lx.safe_name(bad)

    def test_asset_path_guard(self) -> None:
        for bad in ("../x.png", "a/../../x.png", "/etc/passwd", "", ".hid.png", "-x.png"):
            with self.assertRaises(lx.LatexError, msg=bad):
                self.lx.put_asset("", "book", bad, b"x")
        self.lx.put_asset("", "book", "ok/a.png", b"x")
        self.assertEqual(self.lx.assets("", "book"), [{"path": "ok/a.png", "size": 1}])
        outside = Path(self.tmp.name) / "secret"
        outside.write_text("s")
        (self.lx.asset_dir("", "book") / "link").symlink_to(outside.parent)
        with self.assertRaises(lx.LatexError):
            self.lx.put_asset("", "book", "link/pwn.txt", b"x")
        self.assertFalse((outside.parent / "pwn.txt").exists())
        self.lx.delete_asset("", "book", "ok/a.png")
        self.assertNotIn("ok/a.png", [a["path"] for a in self.lx.assets("", "book")])

    def test_engine_setting(self) -> None:
        p = self.lx.create("book")
        self.assertEqual(p["engine"], "pdflatex")
        self.lx.set_engine("", "book", "xelatex")
        self.assertEqual(self.lx.engine_of("", "book"), "xelatex")
        with self.assertRaises(lx.LatexError):
            self.lx.set_engine("", "book", "perl")

    def test_markdown_processing_skips_latex_sources(self) -> None:
        self.assertTrue(all(is_latex_source(t) for t in ("a.tex", "A.BIB", "x.sty", "y.cls")))
        self.assertFalse(is_latex_source("notes.md") or is_latex_source("tex") or is_latex_source(None))
        tex = self.doc("t.tex", "see #todo and [[Target]]")
        self.assertEqual(self.docs.get(tex["id"])["tags"], [])
        note = self.doc("n.md", "see #todo")
        self.assertEqual(self.docs.get(note["id"])["tags"], ["todo"])
        target = self.doc("Target")
        self.assertEqual(self.docs.backlinks(target["id"]), [])
        self.doc("linker.md", "[[Target]]")
        self.assertEqual([b["title"] for b in self.docs.backlinks(target["id"])], ["linker.md"])
        self.assertEqual(self.docs.get(tex["id"])["title"], "t.tex")  # no suffix appended or stripped

    def test_upload_files_sources_become_docs_and_the_rest_assets(self) -> None:
        p = self.lx.create("book")
        a = self.lx.upload("", "book", "intro.tex", b"intro")
        self.assertEqual((a["kind"], a["path"]), ("doc", "intro.tex"))
        d = self.docs.get(a["doc_id"])
        self.assertEqual((d["title"], d["folder"], d["content"]), ("intro.tex", "book", "intro"))
        sub = self.lx.upload("", "book", "chapters/two.bib", "caf\xe9".encode("latin-1"))  # not UTF-8: still readable
        self.assertEqual(self.docs.get(sub["doc_id"])["folder"], "book/chapters")
        self.assertEqual(self.docs.get(sub["doc_id"])["content"], "caf\xe9")
        self.assertEqual(self.lx.upload("", "book", "fig.png", b"png")["kind"], "asset")
        self.assertEqual([x["path"] for x in self.lx.assets("", "book")], ["fig.png"])
        self.assertIn("intro.tex", [f["path"] for f in self.lx.context(p["main_doc_id"])["files"]])

    def test_upload_clash_replaces_the_untouched_template_only(self) -> None:
        p = self.lx.create("book")
        r = self.lx.upload("", "book", "main.tex", b"\\documentclass{book}")
        self.assertEqual((r["replaced"], r["doc_id"]), (True, p["main_doc_id"]))
        self.assertEqual(self.docs.get(p["main_doc_id"])["content"], "\\documentclass{book}")
        r2 = self.lx.upload("", "book", "main.tex", b"other")  # now it is the user's text
        self.assertFalse(r2["replaced"])
        self.assertEqual(self.docs.get(r2["doc_id"])["title"], "main (uploaded).tex")
        self.assertEqual(self.docs.get(p["main_doc_id"])["content"], "\\documentclass{book}")
        r3 = self.lx.upload("", "book", "main.tex", b"third")
        self.assertEqual(self.docs.get(r3["doc_id"])["title"], "main (uploaded 2).tex")

    def test_old_source_assets_convert_once_and_stay_on_failure(self) -> None:
        p = self.lx.create("book")
        self.lx.put_asset("", "book", "intro.tex", b"intro")
        self.lx.put_asset("", "book", "chapters/refs.bib", b"@book{a}")
        self.lx.put_asset("", "book", "fig.png", b"png")
        self.lx.put_asset("", "book", "main.tex", b"\\documentclass{book}")  # clashes with the untouched template
        orig = self.lx.import_source
        def flaky(scope, folder, rel, data):
            if rel == "intro.tex":
                raise RuntimeError("disk full")
            return orig(scope, folder, rel, data)
        self.lx.import_source = flaky  # type: ignore[method-assign]
        self.assertEqual(self.lx.convert_assets("", "book"), 2)
        self.assertEqual(sorted(a["path"] for a in self.lx.assets("", "book")), ["fig.png", "intro.tex"])  # the failed one is kept
        self.lx.import_source = orig  # type: ignore[method-assign]
        ctx = self.lx.context(p["main_doc_id"])  # converts the rest
        self.assertEqual(sorted(f["path"] for f in ctx["files"]), ["chapters/refs.bib", "intro.tex", "main.tex"])
        self.assertEqual([a["path"] for a in ctx["assets"]], ["fig.png"])
        self.assertEqual(self.docs.get(p["main_doc_id"])["content"], "\\documentclass{book}")
        self.assertEqual(self.lx.convert_assets("", "book"), 0)  # idempotent
        self.assertEqual(len([d for d in self.docs.list(None) if d["title"].startswith("intro")]), 1)
        build, ids = self.lx.materialize(self.lx.project_for(p["main_doc_id"]))
        self.assertEqual((build / "intro.tex").read_text(), "intro")

    @unittest.skipUnless(HAVE_TEX, "latexmk is not installed")
    def test_compile_ok_and_error_maps_to_the_doc(self) -> None:
        p = self.lx.create("book")
        res = asyncio.run(self.lx.compile(p))
        self.assertTrue(res["ok"], res)
        self.assertTrue((self.lx.pdf_path(p)).read_bytes().startswith(b"%PDF"))
        ch = self.doc("one.tex", "\\nonsense\n", "book/chapters")
        main = self.docs.get(p["main_doc_id"])
        self.docs.save(main["id"], main["content"].replace("Write here.", "\\input{chapters/one}"))
        bad = asyncio.run(self.lx.compile(self.lx.project_for(main["id"])))
        self.assertFalse(bad["ok"])
        e = bad["errors"][0]
        self.assertEqual((e["file"], e["line"], e["doc_id"]), ("chapters/one.tex", 1, ch["id"]))
        self.assertIn("Undefined control sequence", e["message"])

    @unittest.skipUnless(HAVE_TEX, "latexmk is not installed")
    def test_compile_ignores_rc_files(self) -> None:
        p = self.lx.create("rc")
        self.lx.put_asset("", "rc", "latexmkrc", b'system("touch marker");\n')  # an upload can land in the build folder under any plain name
        self.assertTrue(asyncio.run(self.lx.compile(p))["ok"])
        build = self.lx.build_dir("", "rc")
        self.assertTrue((build / "latexmkrc").exists())
        self.assertFalse((build / "marker").exists())

    @unittest.skipUnless(HAVE_TEX, "latexmk is not installed")
    def test_latexmk_is_run_with_norc(self) -> None:
        seen: list[tuple[str, ...]] = []
        real = asyncio.create_subprocess_exec

        async def spy(*cmd, **kw):  # noqa: ANN002, ANN003
            seen.append(cmd)
            return await real(*cmd, **kw)

        lx.asyncio.create_subprocess_exec = spy  # type: ignore[assignment]
        self.addCleanup(setattr, lx.asyncio, "create_subprocess_exec", real)
        asyncio.run(self.lx.compile(self.lx.create("flags")))
        self.assertTrue({"-norc", "-no-shell-escape"} <= set(seen[0]))


if __name__ == "__main__":
    unittest.main()
