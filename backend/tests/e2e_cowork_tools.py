"""Live checks for the cowork backend tools, run for real on this Mac (no app server, no stubs).

    backend/.venv/bin/python backend/tests/e2e_cowork_tools.py [section ...] [--scratch DIR]

Sections: shell, env, desk, read, deliver, vision, fetch (default: all, in that order; `desk`..`vision` need `env`
and later sections reuse the files `desk` built, so keep --scratch the same between partial runs).
Needs: macOS with sandbox-exec, uv, pandoc, poppler (pdftoppm, pdftotext), tesseract, and real network access (PyPI,
example.com, a public file host). LibreOffice is optional: without it the office previews must answer with the
"soffice is missing" note, which is asserted. If a model proxy answers at http://localhost:4000 the real vision path
is only probed, not driven: that check is reported as SKIP. Not collected by pytest; prints PASS/FAIL/SKIP lines and
exits non-zero when anything failed. Everything lives under the scratch dir (PERSONAL_OS_DATA_DIR is set to it before
anything imports), never the user's data dir. The work environment build needs a minute or two on first run.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

args = list(sys.argv[1:])
SCRATCH = Path(tempfile.mkdtemp(prefix="grain-e2e-cowork-"))
if "--scratch" in args:
    i = args.index("--scratch")
    SCRATCH = Path(args[i + 1]).resolve()
    del args[i:i + 2]
SCRATCH.mkdir(parents=True, exist_ok=True)
(SCRATCH / "data").mkdir(exist_ok=True)
os.environ["PERSONAL_OS_DATA_DIR"] = str(SCRATCH / "data")  # before any personal_os import
os.environ["GRAIN_SECRETS_BACKEND"] = "file"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import envs, shell  # noqa: E402
from personal_os.cowork import Desks  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402
from personal_os.working import ToolResults  # noqa: E402

SECTIONS = args or ["shell", "env", "desk", "read", "deliver", "vision", "fetch"]
results: list[tuple[str, str, str]] = []
DESK = "desk-e2e"
LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)


def check(name: str, ok: bool, evidence: Any = "") -> bool:
    results.append((name, "PASS" if ok else "FAIL", str(evidence)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{str(evidence)[:300]}]" if evidence != "" else ""), flush=True)
    return bool(ok)


def skip(name: str, why: str) -> None:
    results.append((name, "SKIP", why))
    print(f"SKIP {name}  [{why}]", flush=True)


class Box:
    def __init__(self, desk: str = DESK, **settings: Any) -> None:
        self.settings: dict[str, Any] = {"workspaceRoots": [], "deskShellAuto": True, **settings}
        self.db = Database(SCRATCH / "data")
        with self.db.tx() as c:
            c.execute("INSERT OR IGNORE INTO conversations(id, title, model, created_at, updated_at) VALUES(?,?,?,?,?)",
                      ("c1", "t", "m", 0.0, 0.0))
        self.ws = Workspace(SCRATCH / "workspaces")
        self.tb = Toolbox(None, None, None, lambda: self.settings, results=ToolResults(self.db), workspace=self.ws,
                          desks=Desks(self.db, self.ws))  # type: ignore[arg-type]
        self.work_env = envs.WorkEnv(SCRATCH / "data", lambda: self.settings)
        self.tb.work_env = self.work_env
        self.ctx: dict[str, Any] = {"conversation_id": "c1", "desk_id": desk, "message_id": None, "settings": self.settings,
                                    "tainted": False, "taint_sources": []}
        self.root = self.ws.ensure(desk)

    def run(self, name: str, **a: Any) -> Any:
        # One loop for the whole script: background shell jobs are tasks on it and must survive between calls, as they do in the app.
        return LOOP.run_until_complete(self.tb.call(name, a, self.ctx))

    def sh(self, command: str, **a: Any) -> Any:
        return self.run("shell_run", command=command, **a)

    def reset_taint(self) -> None:
        self.ctx["tainted"], self.ctx["taint_sources"] = False, []


def net(r: dict[str, Any], key: str) -> list[str]:
    n = r.get("network")
    return list(n.get(key, [])) if isinstance(n, dict) else []


# ------------------------------------------------------------------ 1. shell egress
def sec_shell() -> None:
    b = Box()
    ensure_env(b)  # `pip` on the shell PATH is the work environment's
    r = b.sh("curl -sS https://pypi.org/simple/pip/ | head -c 200")
    check("shell: registry host reachable through the proxy", r.get("exit_code") == 0 and "pip" in r.get("output", "").lower(),
          {"exit": r.get("exit_code"), "network": r.get("network"), "error": r.get("error"), "out": r.get("output", "")[:60]})
    check("shell: contacted host recorded and the reply tainted", "pypi.org" in net(r, "contacted") and b.ctx["tainted"], net(r, "contacted"))
    b.reset_taint()
    r = b.sh("curl -sS -m 10 https://example.com")
    check("shell: unlisted host blocked and named", r.get("exit_code") != 0 and "example.com" in net(r, "blocked"),
          {"exit": r.get("exit_code"), "blocked": net(r, "blocked")})
    check("shell: the note says how to allow it", "allow" in str(r.get("note", "")).lower(), r.get("note"))
    b2 = Box(shellAllowedDomains=["example.com"])
    r = b2.sh("curl -sS -m 15 https://example.com | head -c 600")
    check("shell: allowed domain succeeds", r.get("exit_code") == 0 and "Example Domain" in r.get("output", ""),
          {"exit": r.get("exit_code"), "out": r.get("output", "")[:50], "net": r.get("network")})
    for label, cmd in (("curl --noproxy", "curl -sS -m 8 --noproxy '*' https://example.com"),
                       ("raw socket", "python3 -c \"import socket; socket.create_connection(('93.184.216.34', 443), 5)\""),
                       ("nc", "nc -z -w 4 93.184.216.34 443")):
        r = b2.sh(cmd)
        check(f"shell: proxy-ignoring {label} cannot connect", r.get("exit_code") not in (0, None),
              {"exit": r.get("exit_code"), "out": r.get("output", "")[:100]})
    b.reset_taint()
    r = b.sh("mkdir -p work/wheels && pip download --no-deps -q -d ./work/wheels six 2>&1 | tail -3; ls work/wheels")
    check("shell: pip download works through the proxy", "six" in r.get("output", "") and any((b.root / "work" / "wheels").glob("six*")),
          r.get("output", "")[-200:])
    r = b.sh("python -c \"import pandas, openpyxl; print('libs ok')\" 2>&1 | tail -2; which python pip")
    check("shell: `python` is the work environment's, libraries import under the shell sandbox", "libs ok" in r.get("output", "") and "envs/work/bin" in r.get("output", ""),
          r.get("output", "")[-200:])
    b3 = Box(shellRegistryAccess=False, shellAllowedDomains=[])
    r = b3.sh("curl -sS -m 6 https://pypi.org/simple/pip/ | head -c 50")
    check("shell: registry off + no hosts = no network", not r.get("output", "").strip() or "pip" not in r.get("output", "").lower(),
          {"exit": r.get("exit_code"), "net": r.get("network"), "out": r.get("output", "")[:80]})
    b = Box(desk="desk-cwd")
    b.sh("mkdir -p work && cd work")
    r = b.sh("pwd")
    check("shell: cd persists to the next call", r.get("output", "").strip().endswith("/work"), r.get("output", "").strip())
    b.sh("export X=1")
    r = b.sh("echo \"x=[$X]\"")
    check("shell: export does not persist", r.get("output", "").strip() == "x=[]", r.get("output", "").strip())
    b.sh("cd " + str(b.root))
    r = b.sh("sleep 5; echo done-bg", timeout_s=2)
    jid = r.get("job_id")
    check("shell: timeout promotes to a background job", bool(jid) and r.get("still_running") is True,
          {k: r.get(k) for k in ("job_id", "still_running", "note")})
    if jid:
        deadline, st, seen = time.time() + 15, {}, ""
        while time.time() < deadline:  # each poll returns only what is new, so collect across polls
            st = b.run("shell_poll", job_id=jid)
            seen += st.get("output", "")
            if st.get("status") != "running":
                break
            time.sleep(0.5)
        check("shell: shell_poll shows the job finish", st.get("status") == "exited" and "done-bg" in seen, {"status": st.get("status"), "seen": seen})
        check("shell: completion notice drained", len(b.tb.shell.drain_notes("c1")) == 1)
    r = b.sh("sleep 20", timeout_s=2, on_timeout="kill")
    check("shell: on_timeout=kill kills it", r.get("timed_out") is True and not r.get("job_id"),
          {k: r.get(k) for k in ("timed_out", "exit_code", "note")})
    outside = Path.home() / "grain-e2e-should-not-exist.txt"
    r = b.sh(f"echo hi > {outside}; echo rc=$?")
    check("shell: write outside the workspace fails", not outside.exists() and "rc=0" not in r.get("output", ""), r.get("output", "")[:150])
    outside.unlink(missing_ok=True)
    r = b.sh("ls ~/.ssh 2>&1; echo rc=$?")
    check("shell: reading ~/.ssh fails", "rc=0" not in r.get("output", ""), r.get("output", "")[:150])
    roots = [b.root]
    ok = shell.auto_ok({"command": "ls"}, b.ctx, b.settings, roots)
    check("shell.auto_ok: desk + default settings + clean reply -> no card", ok is True, ok)
    b.ctx["tainted"] = True
    check("shell.auto_ok: tainted reply with proxy reachable -> card", shell.auto_ok({"command": "ls"}, b.ctx, b.settings, roots) is False)
    b.reset_taint()
    check("shell.auto_ok: unsandboxed -> card", shell.auto_ok({"command": "ls", "unsandboxed": True}, b.ctx, b.settings, roots) is False)
    check("shell.auto_ok: open network -> card", shell.auto_ok({"command": "ls"}, b.ctx, {**b.settings, "shellNetwork": True}, roots) is False)
    check("shell.auto_ok: deskShellAuto off -> card", shell.auto_ok({"command": "ls"}, b.ctx, {**b.settings, "deskShellAuto": False}, roots) is False)
    check("shell.auto_ok: cwd outside the workspace -> card", shell.auto_ok({"command": "ls", "cwd": "/tmp"}, b.ctx, b.settings, roots) is False)
    check("shell.auto_ok: not a desk -> card", shell.auto_ok({"command": "ls"}, {**b.ctx, "desk_id": None}, b.settings, roots) is False)


# ------------------------------------------------------------------ 2. work environment
def sec_env() -> None:
    b = Box()
    e = b.work_env
    shutil.rmtree(e.dir, ignore_errors=True)
    t0 = time.time()
    st = e.ensure()
    build = time.time() - t0
    check("env: ensure() builds a ready venv", st["ready"] and not st["error"], f"{build:.1f}s, installer={st['installer']}, error={st['error']}")
    p = subprocess.run([st["python"] or "python3", "-c", "import pandas, openpyxl, docx, pptx, pypdf, pdfplumber, reportlab, matplotlib, PIL, xlsxwriter; print('ok')"],
                       capture_output=True, text=True)
    check("env: base packages import", p.stdout.strip() == "ok", p.stderr[-300:] or p.stdout)
    t0 = time.time()
    st2 = e.ensure()
    check("env: second ensure() is a fast no-op", st2["ready"] and time.time() - t0 < 1.0, f"{time.time() - t0:.3f}s")
    t0 = time.time()
    st3 = e.install(["tabulate"])
    check("env: install(['tabulate']) adds a package", st3["ready"] and not st3["error"] and "tabulate" in st3["packages"],
          f"{time.time() - t0:.1f}s {st3['error']}")
    p = subprocess.run([e.python_path(), "-c", "import tabulate; print('ok')"], capture_output=True, text=True)
    check("env: tabulate importable", p.stdout.strip() == "ok", p.stderr[-200:])
    calls: list[Any] = []
    real = e.runner
    e.runner = lambda *a, **k: calls.append(a) or real(*a, **k)
    try:
        e.install(["--index-url=http://evil"])
        refused = False
    except envs.EnvError:
        refused = True
    e.runner = real
    check("env: a bad name is refused before any command runs", refused and not calls)


# ------------------------------------------------------------------ 3. run_python in a desk
BUILD_SCRIPT = r'''
import os
os.makedirs("outputs", exist_ok=True)
import openpyxl
from openpyxl.styles import Font
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Model"
ws.append(["Item", "Qty", "Price"]); ws.append(["Widget", 3, 9.5]); ws.append(["Gadget", 2, 20.25])
ws["D1"] = "Total"; ws["D2"] = "=B2*C2"; ws["D3"] = "=B3*C3"; ws["D4"] = "=SUM(D2:D3)"
for c in ("C2", "C3", "D2", "D3", "D4"): ws[c].number_format = '"$"#,##0.00'
ws["A1"].font = Font(bold=True)
wb.save("outputs/model.xlsx")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(4, 3)); ax.bar(["Widget", "Gadget"], [28.5, 40.5]); ax.set_title("Revenue by item")
fig.savefig("outputs/chart.png", dpi=150)
import docx
d = docx.Document(); d.add_heading("Quarterly Report", 0); d.add_paragraph("Revenue grew strongly this quarter.")
t = d.add_table(rows=2, cols=2); t.cell(0, 0).text = "Item"; t.cell(0, 1).text = "Revenue"; t.cell(1, 0).text = "Widget"; t.cell(1, 1).text = "28.50"
d.add_picture("outputs/chart.png"); d.save("outputs/report.docx")
import pptx
pr = pptx.Presentation()
for i, title in enumerate(["Overview", "Numbers", "Next steps"]):
    s = pr.slides.add_slide(pr.slide_layouts[1]); s.shapes.title.text = title; s.placeholders[1].text = f"Slide {i + 1} body text"
pr.save("outputs/deck.pptx")
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
cv = canvas.Canvas("outputs/summary.pdf", pagesize=letter); cv.setFont("Helvetica", 18); cv.drawString(72, 700, "Summary of results")
cv.setFont("Helvetica", 12); cv.drawString(72, 670, "Widget revenue was 28.50 and gadget revenue was 40.50."); cv.save()
print("built")
'''


def ensure_env(b: Box) -> None:
    if not b.work_env.status()["ready"]:
        b.work_env.ensure()


def sec_desk() -> None:
    b = Box()
    ensure_env(b)
    r = b.run("run_python", code=BUILD_SCRIPT, timeout=120)
    check("desk: build script exits 0 under Seatbelt with the venv python", r.get("exit_code") == 0 and "built" in r.get("stdout", ""),
          {"exit": r.get("exit_code"), "stderr": r.get("stderr", "")[-400:]})
    want = ["outputs/model.xlsx", "outputs/report.docx", "outputs/deck.pptx", "outputs/chart.png", "outputs/summary.pdf"]
    sizes = {w: (b.root / w).stat().st_size if (b.root / w).exists() else None for w in want}
    check("desk: all five files exist, non-empty", all(sizes.values()), sizes)
    check("desk: all reported in workspace_files", all(w in r.get("workspace_files", []) for w in want), r.get("workspace_files"))
    r = b.run("run_python", timeout=30, code="import socket\ntry:\n    socket.create_connection(('example.com', 80), 4); print('CONNECTED')\n"
              "except OSError as e:\n    print('blocked', type(e).__name__)\n")
    check("desk: no network from the script", "blocked" in r.get("stdout", "") and "CONNECTED" not in r.get("stdout", ""),
          r.get("stdout", "") + r.get("stderr", "")[-200:])
    outside = Path.home() / "grain-e2e-py-should-not-exist.txt"
    r = b.run("run_python", timeout=30, code=f"try:\n    open({str(outside)!r}, 'w').write('x'); print('WROTE')\nexcept OSError as e:\n    print('refused', type(e).__name__)\n")
    check("desk: write outside the workspace fails", "refused" in r.get("stdout", "") and not outside.exists(), r.get("stdout", ""))
    outside.unlink(missing_ok=True)


# ------------------------------------------------------------------ 4. read back
def sec_read() -> None:
    b = Box()
    exp = {"outputs/model.xlsx": ["Widget", "Gadget"], "outputs/report.docx": ["Quarterly Report", "Widget"],
           "outputs/deck.pptx": ["Overview", "Next steps"], "outputs/summary.pdf": ["Summary of results"]}
    for path, needles in exp.items():
        r = b.run("desk_read_file", path=path)
        text = str(r.get("text", ""))
        check(f"read: desk_read_file {path}", all(n in text for n in needles) and not r.get("error"),
              (r.get("error") or text[:120]).replace("\n", " | "))
    (b.root / "work").mkdir(exist_ok=True)
    (b.root / "work" / "n.txt").write_text("alpha\nbeta\n")
    b.run("desk_read_file", path="work/n.txt")
    r = b.run("fs_edit", path="work/n.txt", old="beta", new="BETA")
    check("read: fs_edit right after desk_read_file", r.get("replacements") == 1 and (b.root / "work" / "n.txt").read_text() == "alpha\nBETA\n", r)


# ------------------------------------------------------------------ 5. deliverables
def sec_deliver() -> None:
    b = Box()
    ensure_env(b)
    from personal_os import deliver
    for fmt in deliver.GUIDE_FORMATS:
        g = b.run("doc_guide", format=fmt)
        text = g.get("guide") or g.get("text") or ""
        check(f"deliver: doc_guide {fmt} returns a guide", bool(text) and not g.get("error"), len(text))
        blocks = re.findall(r"```python\n(.*?)```", text, re.S)
        if not blocks:
            skip(f"deliver: doc_guide {fmt} skeleton runs", "no python block")
            continue
        sub = Box(desk=f"guide-{fmt}")
        (sub.root / "outputs").mkdir(exist_ok=True)
        r = sub.run("run_python", code="\n".join(blocks), timeout=90)
        made = r.get("workspace_files", [])
        check(f"deliver: doc_guide {fmt} skeleton runs", r.get("exit_code") == 0, {"files": made, "stderr": r.get("stderr", "")[-300:]})
        for f in made:
            if f.endswith((".docx", ".xlsx", ".pptx", ".pdf")):
                rd = sub.run("desk_read_file", path=f)
                check(f"deliver: {fmt} skeleton output {f} reads back", bool(str(rd.get("text", "")).strip()) and not rd.get("error"),
                      str(rd.get("error") or rd.get("text", ""))[:100])
    r = b.run("convert_document", path="outputs/report.docx", to="md")
    md = (b.root / r["output"]).read_text() if not r.get("error") else ""
    check("deliver: docx -> md (pandoc) keeps title, table and a resolvable image", "Quarterly Report" in md and "Widget" in md
          and (b.root / "outputs" / "report_media" / "media" / "image1.png").is_file(), md[:200].replace("\n", " | "))
    (b.root / "work").mkdir(exist_ok=True)
    (b.root / "work" / "n.md").write_text("# Title\n\nSome *text* here.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    r = b.run("convert_document", path="work/n.md", to="docx")
    check("deliver: md -> docx (pandoc)", not r.get("error") and (b.root / r["output"]).stat().st_size > 1000, r)
    r = b.run("convert_document", path="outputs/summary.pdf", to="txt")
    check("deliver: pdf -> txt", not r.get("error") and "Summary of results" in (b.root / r["output"]).read_text(), r)
    r = b.run("render_preview", path="outputs/summary.pdf")
    pngs = list((b.root / "work" / "previews").glob("*.png"))
    check("deliver: render_preview pdf -> PNGs in work/previews", not r.get("error") and pngs, {"n": len(pngs), "r": str(r)[:200]})
    for f in ("outputs/report.docx", "outputs/model.xlsx", "outputs/deck.pptx"):
        r = b.run("render_preview", path=f)
        if shutil.which("soffice"):
            check(f"deliver: render_preview {f} with soffice", not r.get("error"), r)
        else:
            check(f"deliver: render_preview {f} says soffice is missing, no crash", "soffice" in str(r).lower() or "libreoffice" in str(r).lower(),
                  str(r)[:200])


# ------------------------------------------------------------------ 6. vision / OCR
def sec_vision() -> None:
    b = Box()
    from PIL import Image, ImageDraw, ImageFont
    from personal_os import extract_text
    (b.root / "work").mkdir(exist_ok=True)
    img = Image.new("RGB", (900, 300), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 56)
    except OSError:
        font = ImageFont.load_default()
    d.text((30, 60), "INVOICE 4821", fill="black", font=font)
    d.text((30, 160), "Total due 1290 dollars", fill="black", font=font)
    img.save(b.root / "work" / "text.png")
    r = b.run("view_image", path="work/text.png")
    txt = str(r.get("text") or r.get("description") or r)
    check("vision: view_image OCRs an image with clear text", r.get("ocr") is True and "4821" in txt, {"ocr": r.get("ocr"), "text": txt[:120]})
    if (b.root / "outputs" / "chart.png").exists():
        r = b.run("view_image", path="outputs/chart.png")
        check("vision: view_image on chart.png OCRs the title", r.get("ocr") is True and "Revenue" in str(r.get("text") or r.get("description")), str(r)[:200])
    previews = sorted((b.root / "work" / "previews").glob("*.png"))
    if previews:
        r = b.run("view_image", path=str(previews[0].relative_to(b.root)))
        check("vision: view_image on a rendered preview page", "Summary" in str(r.get("text") or r.get("description")), str(r)[:200])
    pdf = b.root / "outputs" / "summary.pdf"
    if pdf.exists() and shutil.which("pdftoppm"):
        subprocess.run(["pdftoppm", "-r", "150", "-png", "-singlefile", str(pdf), str(b.root / "work" / "scan")], check=True)
        Image.open(b.root / "work" / "scan.png").convert("RGB").save(b.root / "work" / "scanned.pdf")
        t0 = time.time()
        res = extract_text.extract_text("scanned.pdf", (b.root / "work" / "scanned.pdf").read_bytes())
        el = time.time() - t0
        check("vision: scanned (image-only) PDF is OCRed by extract_text within its limits", "Summary of results" in res and el < extract_text.OCR_TOTAL_TIMEOUT_S,
              f"{el:.1f}s {res[:100]!r}")
        res = b.run("desk_read_file", path="work/scanned.pdf")
        check("vision: desk_read_file on the scanned PDF returns OCR text", "Summary of results" in str(res.get("text", "")),
              str(res.get("error") or res.get("text", ""))[:100])
    else:
        skip("vision: scanned PDF OCR", "summary.pdf or pdftoppm missing (run the desk section first)")
    c = Box(desk="desk-budget")
    (c.root / "work").mkdir(exist_ok=True)
    shutil.copy(b.root / "work" / "text.png", c.root / "work" / "text.png")
    first = c.run("view_image", path="work/text.png")
    again = c.run("view_image", path="work/text.png")
    check("vision: an identical second call is served from the cache and costs no budget", again.get("cached") is True and c.ctx["_view_image_calls"] == 1,
          {"cached": again.get("cached"), "calls": c.ctx.get("_view_image_calls")})
    last: Any = None
    n_ok = 1
    for i in range(14):
        last = c.run("view_image", path="work/text.png", question=f"q{i}")
        n_ok += not last.get("error")
    check("vision: the 12-call budget stops later calls in one reply", n_ok == 12 and bool(last.get("error")) and "12" in str(last.get("error")),
          {"ok_calls": n_ok, "last": str(last.get("error"))[:100]})
    # The real vision path, when a model proxy answers. The key comes from a .env file (GRAIN_ENV_FILE, else the repo's own)
    # and is never printed.
    import httpx
    key = ""
    for cand in (Path(os.environ.get("GRAIN_ENV_FILE", "/nonexistent")), Path(__file__).resolve().parents[2] / ".env"):
        if cand.is_file():
            key = next((ln.split("=", 1)[1].strip() for ln in cand.read_text().splitlines() if ln.startswith("LITELLM_MASTER_KEY=")), key)
    try:
        r = httpx.get("http://localhost:4000/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=3)
        models = [m["id"] for m in r.json()["data"]] if r.status_code == 200 else []
    except Exception:  # noqa: BLE001
        models = []
    if not models:
        skip("vision: real vision-model path", "no model proxy answered at localhost:4000 (set GRAIN_ENV_FILE to a .env with LITELLM_MASTER_KEY)")
        return
    for m in ("kimi-k3", "qwen3.8-max"):
        if m in models:
            v = Box(desk="desk-vision", baseUrl="http://localhost:4000/v1", apiKey=key, visionModel=m)
            (v.root / "work").mkdir(exist_ok=True)
            shutil.copy(b.root / "work" / "text.png", v.root / "work" / "text.png")
            r = v.run("view_image", path="work/text.png", question="What invoice number is shown?")
            check(f"vision: real vision model {m} reads the image", "4821" in str(r.get("description")) and not r.get("ocr"),
                  str(r.get("description") or r.get("error"))[:120])
            return
    skip("vision: real vision-model path", f"no known vision-capable model in the proxy listing: {models}")


# ------------------------------------------------------------------ 7. desk_fetch_file
def sec_fetch() -> None:
    b = Box()
    r = b.run("desk_fetch_file", url="https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf")
    p = b.root / str(r.get("path", "")) if r.get("path") else None
    check("fetch: public PDF lands under work/downloads", not r.get("error") and p is not None and p.exists() and "work/downloads" in str(r.get("path")),
          {k: r.get(k) for k in ("path", "bytes", "sha256", "error")})
    check("fetch: bytes and sha reported", bool(r.get("bytes")) and bool(r.get("sha256") or r.get("sha")), r)
    for u in ("http://127.0.0.1:1/x", "http://localhost/x", "http://169.254.169.254/latest/meta-data", "http://192.168.1.1/"):
        b.reset_taint()  # a successful fetch taints the reply, and fetches then refuse for that reason; each case stands alone
        r = b.run("desk_fetch_file", url=u)
        check(f"fetch: private host refused {u}", bool(r.get("error")), str(r.get("error"))[:120])
    b.reset_taint()
    r = b.run("desk_fetch_file", url="https://httpbin.org/redirect-to?url=http%3A%2F%2F127.0.0.1%3A1%2Fx")
    check("fetch: redirect to a private host refused", "private" in str(r.get("error")) or "loopback" in str(r.get("error")), str(r.get("error"))[:200])
    q = Box(desk="desk-quota")
    q.ws.max_total_bytes = 5000  # the sample PDF is 13 KB
    r = q.run("desk_fetch_file", url="https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf")
    check("fetch: the workspace quota is respected", bool(r.get("error")) and not list((q.root / "work" / "downloads").glob("*.pdf")), str(r.get("error"))[:160])


def main() -> int:
    t0 = time.time()
    print(f"scratch: {SCRATCH}", flush=True)
    for s in SECTIONS:
        try:
            globals()[f"sec_{s}"]()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{s}: section ran without raising", False, repr(e)[:200])
    failed = [r for r in results if r[1] == "FAIL"]
    print(f"\n{sum(r[1] == 'PASS' for r in results)} passed, {len(failed)} failed, {sum(r[1] == 'SKIP' for r in results)} skipped in {time.time() - t0:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
