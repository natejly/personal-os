"""LaTeX extension: log parser, path guard, and a real latexmk compile (skipped when TeX is missing)."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from personal_os.extensions.latex import extension as lx

HAVE_TEX = lx.shutil.which("latexmk", path=lx.tex_env()["PATH"]) is not None


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
        w = lx.parse_log(log)["warnings"]
        self.assertEqual([x["line"] for x in w], [7, 9, 20])

    def test_duplicates_and_cap(self) -> None:
        log = "a.tex:1: Boom.\n" * 3 + "".join(f"Overfull \\hbox (1pt) in paragraph at lines {i}--{i}\n" for i in range(80))
        r = lx.parse_log(log)
        self.assertEqual(len(r["errors"]), 1)
        self.assertEqual(len(r["warnings"]), lx.MAX_DIAGNOSTICS)


class ProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["GRAIN_LATEX_DIR"] = str(Path(self.tmp.name) / "LaTeX")
        self.addCleanup(os.environ.pop, "GRAIN_LATEX_DIR", None)

    def test_create_and_list(self) -> None:
        p = lx.create_project("thesis")
        self.assertEqual((p["main"], p["has_pdf"]), ("main.tex", False))
        self.assertIn("\\documentclass{article}", (Path(p["path"]) / "main.tex").read_text())
        self.assertEqual([x["name"] for x in lx.list_projects()], ["thesis"])
        with self.assertRaises(lx.LatexError):
            lx.create_project("thesis")

    def test_bad_names(self) -> None:
        for bad in ("", ".hidden", "a/b", "..", "x" * 65):
            with self.assertRaises(lx.LatexError, msg=bad):
                lx.create_project(bad)

    def test_path_guard(self) -> None:
        proj = lx.project_dir(lx.create_project("p")["name"])
        outside = Path(self.tmp.name) / "secret.txt"
        outside.write_text("s")
        (proj / "link").symlink_to(outside)
        for bad in ("../secret.txt", "a/../../x", "/etc/passwd", "", ".grain-latex.json", ".grain-build/x", "link"):
            with self.assertRaises(lx.LatexError, msg=bad):
                lx.safe_path(proj, bad)
        self.assertEqual(lx.safe_path(proj, "sub/ok.tex"), proj / "sub" / "ok.tex")

    def test_file_list_skips_artefacts(self) -> None:
        proj = lx.project_dir(lx.create_project("p")["name"])
        for f in ("main.aux", "main.log", "refs.bib", "fig.png"):
            (proj / f).write_text("x")
        (proj / lx.BUILD_DIR).mkdir()
        (proj / lx.BUILD_DIR / "main.pdf").write_text("x")
        got = {f["path"]: f["kind"] for f in lx.list_files(proj)}
        self.assertEqual(got, {"main.tex": "tex", "refs.bib": "bib", "fig.png": "image"})

    def test_odd_main_files_fall_back(self) -> None:
        proj = lx.project_dir(lx.create_project("p")["name"])
        (proj.parent / "x.tex").write_text("x")
        (proj / "-x.tex").write_text("\\documentclass{article}")
        for bad in ("../x.tex", "-x.tex", "/etc/passwd", "main.txt", ".hidden.tex"):
            lx.write_settings(proj, {"main": bad})
            self.assertEqual(lx.main_file(proj), "main.tex", bad)
        (proj / "main.tex").unlink()
        self.assertEqual(lx.main_file(proj), "main.tex")  # the "-" file is not picked by the \documentclass fallback

    @unittest.skipUnless(HAVE_TEX, "latexmk is not installed")
    def test_compile_ignores_rc_files(self) -> None:
        proj = lx.project_dir(lx.create_project("rc")["name"])
        for rc in ("latexmkrc", ".latexmkrc"):
            (proj / rc).write_text('system("touch marker");\n')
        self.assertTrue(asyncio.run(lx.compile_project("rc"))["ok"])
        self.assertFalse((proj / "marker").exists())

    @unittest.skipUnless(HAVE_TEX, "latexmk is not installed")
    def test_compile_ok_and_broken(self) -> None:
        lx.create_project("doc")
        res = asyncio.run(lx.compile_project("doc"))
        self.assertTrue(res["ok"], res)
        proj = lx.project_dir("doc")
        self.assertTrue((proj / "main.pdf").read_bytes().startswith(b"%PDF"))
        (proj / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\nhello\n\\nonsense\n\\end{document}\n")
        bad = asyncio.run(lx.compile_project("doc"))
        self.assertFalse(bad["ok"])
        self.assertEqual((bad["errors"][0]["file"], bad["errors"][0]["line"]), ("main.tex", 4))
        self.assertIn("Undefined control sequence", bad["errors"][0]["message"])


if __name__ == "__main__":
    unittest.main()
