"""The desk's file tools working together: read ledger, binary reads, run_python in the workspace, desk_fetch_file.
Seatbelt-dependent assertions use the real sandbox-exec and skip when it is absent; nothing touches the network."""
from __future__ import annotations

import asyncio
import hashlib
import http.server
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="dwt-"))

from personal_os import tools as tools_mod  # noqa: E402
from personal_os.cowork import Desks  # noqa: E402
from personal_os.db import Database  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402
from personal_os.working import ToolResults  # noqa: E402

HAVE_SEATBELT = sys.platform == "darwin" and bool(shutil.which("sandbox-exec"))
needs_seatbelt = pytest.mark.skipif(not HAVE_SEATBELT, reason="sandbox-exec is not available: Seatbelt assertions skipped")
DESK = "desk1"


class Box:
    def __init__(self, tmp: Path, sandboxes: Any = None, **ws_kw: Any) -> None:
        self.settings: dict[str, Any] = {"workspaceRoots": []}
        self.db = Database(tmp / "data")
        with self.db.tx() as c:
            c.execute("INSERT INTO conversations(id, title, model, created_at, updated_at) VALUES(?,?,?,?,?)", ("c1", "t", "m", 0.0, 0.0))
        self.ws = Workspace(Path(os.environ["PERSONAL_OS_DATA_DIR"]) / f"ws-{tmp.name}", **ws_kw)
        self.tb = Toolbox(None, None, None, lambda: self.settings, results=ToolResults(self.db), workspace=self.ws,
                          sandboxes=sandboxes, desks=Desks(self.db, self.ws))  # type: ignore[arg-type]
        self.ctx: dict[str, Any] = {"conversation_id": "c1", "desk_id": DESK, "message_id": None, "settings": self.settings,
                                    "tainted": False, "taint_sources": []}
        self.root = self.ws.ensure(DESK)
        self.keep_taint = False

    def run(self, name: str, **args: Any) -> Any:
        if name == "desk_fetch_file" and not self.keep_taint:
            self.ctx["tainted"] = False  # a successful fetch taints the reply (taints=True); each call here stands alone
        return asyncio.run(self.tb.call(name, args, self.ctx))


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


# ---------------------------------------------------------------- read ledger
def test_read_then_edit_succeeds(box: Box) -> None:
    (box.root / "work" / "n.txt").write_text("alpha\nbeta\ngamma\n")
    refused = box.run("fs_edit", path="work/n.txt", old="beta", new="BETA")
    assert refused["error"] and "desk_read_file" in refused["error"] and "read_local_file" not in refused["error"]
    r = box.run("desk_read_file", path="work/n.txt")
    assert r["text"].startswith("alpha")
    ok = box.run("fs_edit", path="work/n.txt", old="beta", new="BETA")
    assert ok.get("replacements") == 1, ok
    assert (box.root / "work" / "n.txt").read_text() == "alpha\nBETA\ngamma\n"


def test_partial_read_covers_only_its_window(box: Box) -> None:
    (box.root / "work" / "long.txt").write_text("".join(f"line {i}\n" for i in range(400)))
    first = box.run("desk_read_file", path="work/long.txt", length=100)
    assert first["truncated"]
    assert box.run("fs_edit", path="work/long.txt", old="line 399", new="end")["error"]  # outside what was read
    assert box.run("fs_edit", path="work/long.txt", old="line 1\n", new="one\n").get("replacements") == 1


def test_write_then_edit_succeeds(box: Box) -> None:
    box.run("desk_write_file", path="work/w.md", content="# Title\nbody\n")
    assert box.run("fs_edit", path="work/w.md", old="body", new="text").get("replacements") == 1
    box.run("desk_write_file", path="work/w.md", content="more\n", mode="append")
    assert box.run("fs_edit", path="work/w.md", old="more", new="MORE").get("replacements") == 1


def test_edit_outside_a_desk_still_names_read_local_file(tmp_path: Path) -> None:
    from personal_os import fsx
    led = fsx.ReadLedger()
    assert "read_local_file" in led.check("c", tmp_path / "x", 1, [(0, 1)])
    assert "desk_read_file" in led.check("c", tmp_path / "x", 1, [(0, 1)], in_desk=True)


# ---------------------------------------------------------------- binary reads
def test_binary_files_are_extracted_not_refused(box: Box) -> None:
    (box.root / "work" / "thing.bin").write_bytes(b"\x00\x01\x02 data")
    r = box.run("desk_read_file", path="work/thing.bin")
    assert "error" not in r and r["kind"] == "binary" and "text" in r and r["bytes"] == 8
    (box.root / "work" / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    p = box.run("desk_read_file", path="work/pic.png")
    assert p["kind"] == "image" and "view_image" in p["note"]
    # an extracted file is not a text baseline: fs_edit still wants a text read
    (box.root / "work" / "d.docx").write_bytes(b"not really a docx")
    assert box.run("desk_read_file", path="work/d.docx")["kind"] == "document"


def test_extracted_text_is_paged(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("personal_os.extract_text.extract_text", lambda name, data, mime="": "".join(f"row {i}\n" for i in range(500)))
    (box.root / "work" / "r.pdf").write_bytes(b"%PDF-1.4 fake")
    a = box.run("desk_read_file", path="work/r.pdf", length=200)
    assert a["kind"] == "pdf" and a["truncated"] and a["next_offset"] > 0
    b = box.run("desk_read_file", path="work/r.pdf", offset=a["next_offset"], length=200)
    assert b["text"].startswith("row") and b["offset"] == a["next_offset"]


# ---------------------------------------------------------------- run_python
@needs_seatbelt
def test_run_python_writes_into_the_workspace(box: Box) -> None:
    r = box.run("run_python", code="import os\nos.makedirs('outputs', exist_ok=True)\nopen('outputs/x.txt','w').write('hi')\nprint(os.getcwd())")
    assert r["exit_code"] == 0, r
    assert (box.root / "outputs" / "x.txt").read_text() == "hi"
    assert r["workspace_files"] == ["outputs/x.txt"]
    assert os.path.realpath(r["stdout"].strip()) == str(box.root.resolve())
    # reads what is already there, and a second run reports only what it changed
    r2 = box.run("run_python", code="print(open('outputs/x.txt').read())")
    assert r2["stdout"].strip() == "hi" and r2["workspace_files"] == []


@needs_seatbelt
def test_run_python_keeps_its_other_walls(box: Box, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere.txt"
    other.write_text("x")
    r = box.run("run_python", code=f"""
import socket
try:
    open({str(other)!r}, 'a').write('y'); print('wrote-elsewhere')
except OSError:
    print('write-blocked')
try:
    socket.create_connection(('127.0.0.1', 9), timeout=1); print('net-ok')
except OSError:
    print('net-blocked')
""")
    assert "write-blocked" in r["stdout"] and "net-blocked" in r["stdout"], r
    assert other.read_text() == "x"


def test_run_python_outside_a_desk_is_unchanged(box: Box) -> None:
    del box.ctx["desk_id"]
    r = box.run("run_python", code="print(6*7)")
    assert r["stdout"].strip() == "42" and "workspace_files" not in r


def test_run_python_outside_a_desk_keeps_outputs_in_the_chat_files(box: Box) -> None:
    del box.ctx["desk_id"]
    r = box.run("run_python", code="import os\nos.makedirs('outputs', exist_ok=True)\nopen('outputs/a.csv','w').write('x,y\\n')\n"
                                   "for i in range(12):\n    open(f'outputs/n{i:02d}.txt','w').write(str(i))\nopen('scratch.txt','w').write('tmp')\nprint(os.getcwd())")
    assert r["exit_code"] == 0, r
    chat = box.tb.chat_outputs.desk_root("c1")
    assert (chat / "outputs" / "a.csv").read_text() == "x,y\n"
    assert r["outputs"][0] == {"name": "a.csv", "size": 4, "path": "outputs/a.csv"} and len(r["outputs"]) == 10
    assert not (chat / "outputs" / "scratch.txt").exists() and not (chat / "scratch.txt").exists()  # only outputs/ is kept
    assert not os.path.exists(r["stdout"].strip())  # the temp dir is still deleted
    assert not (box.ws.desk_root(DESK) / "outputs" / "a.csv").exists()
    # nothing saved under outputs/: no outputs key, as before
    assert "outputs" not in box.run("run_python", code="print(1)")


def test_a_long_run_python_preview_keeps_every_output_for_the_card(box: Box) -> None:
    """files_created lists the same files as outputs; when stdout pushes the preview past its limit, the
    trimmed list must be files_created, never the outputs the card offers for download."""
    import json
    del box.ctx["desk_id"]
    for n in (450, 1000):  # 450: a list is trimmed; 1000: no trim fits, the string cut keeps outputs first
        r = box.run("run_python", code="import os\nos.makedirs('outputs', exist_ok=True)\n"
                                       f"for i in range(5):\n    open(f'outputs/chart{{i}}.png','w').write(str(i))\nprint('x' * {n})")
        assert r["exit_code"] == 0, r
        assert len(r["outputs"]) == 5 and len(r["files_created"]) == 5
        assert len(json.dumps(r)) > 1500
        shown = json.loads(tools_mod.summarize_result(r))
        if n == 450:
            assert shown["truncated"]["field"] != "outputs" and shown["outputs"] == r["outputs"]
        else:  # the card reads the cut preview loosely from its head
            assert shown["truncated"] is True
            assert shown["preview"].startswith(json.dumps({"outputs": r["outputs"]})[:-1])


def test_run_python_warns_when_the_workspace_is_over_quota(tmp_path: Path) -> None:
    b = Box(tmp_path, max_total_bytes=50)
    (b.root / "work" / "big.bin").write_bytes(b"x" * 200)
    r = b.run("run_python", code="print(1)")
    assert "warning" in r and "desk_trash_file" in r["warning"]
    assert (b.root / "work" / "big.bin").exists()  # never deletes


def test_desk_tools_include_run_python_for_snapshots() -> None:
    from personal_os.snapshots import DESK_TOOLS
    assert {"run_python", "desk_fetch_file"} <= DESK_TOOLS


def test_sandbox_profile_allows_workspace_after_the_data_dir_deny(tmp_path: Path) -> None:
    from personal_os import sandbox
    prof = sandbox._mac_profile(str(tmp_path), sys.executable, None, str(tmp_path / "ws"))
    ws_allow = f'(allow file-read* file-write* (subpath "{os.path.realpath(tmp_path / "ws")}"))'
    assert ws_allow in prof
    data = sandbox._paths()[2]
    assert prof.index(ws_allow) > prof.index(f'(subpath "{data}")')  # last match wins
    assert prof.rstrip().endswith('(regex #"/personal-os\\.db"))')  # the secret-name denies still come last
    assert ws_allow not in sandbox._mac_profile(str(tmp_path), sys.executable)


# ---------------------------------------------------------------- desk_fetch_file
def test_fetch_refuses_loopback(box: Box) -> None:
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"secret")

        def log_message(self, *a: Any) -> None:
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = box.run("desk_fetch_file", url=f"http://127.0.0.1:{srv.server_address[1]}/a.txt")
    finally:
        srv.shutdown()
    assert "refused" in r["error"] and not (box.root / "work" / "downloads").exists()
    assert "refused" in box.run("desk_fetch_file", url="file:///etc/passwd")["error"]


def fake_open(responses: dict[str, httpx.Response], seen: list[str] | None = None) -> Any:
    async def opener(client: Any, url: str, host: str) -> httpx.Response:
        if seen is not None:
            seen.append(url)
        return responses[url]
    return opener


def test_a_token_in_a_desk_file_is_stripped_for_the_model(box: Box) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    box.run("desk_write_file", path="work/secret.md", content=f"key {pat}\n")
    got = box.run("desk_read_file", path="work/secret.md")
    assert pat not in got["text"] and "[github-pat]" in got["text"]
    assert (box.root / "work" / "secret.md").read_text() == f"key {pat}\n"


def test_reading_a_download_taints_again_after_the_chat_is_cleared(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://example.com/note.txt"
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open({
        url: httpx.Response(200, content=b"hello from the web\n", headers={"content-type": "text/plain"}),
    }))
    saved = box.run("desk_fetch_file", url=url)
    assert saved["path"].endswith("note.txt")
    assert box.ws.was_fetched(DESK, saved["path"])
    box.ctx["tainted"] = False
    box.ctx["taint_sources"] = []
    got = box.run("desk_read_file", path=saved["path"])
    assert "hello from the web" in got["text"]
    assert box.ctx["tainted"] is True and "desk_read_file" in box.ctx["taint_sources"]
    box.ctx["tainted"] = False
    box.ctx["taint_sources"] = []
    box.run("desk_write_file", path="work/own.md", content="mine\n")
    own = box.run("desk_read_file", path="work/own.md")
    assert own["text"].startswith("mine") and box.ctx["tainted"] is False
    assert box.ws.was_fetched(DESK, "work/own.md") is False


def test_copying_a_download_to_a_new_path_stays_untrusted(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://example.com/note.txt"
    body = b"hello from the web\n"
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open({
        url: httpx.Response(200, content=body, headers={"content-type": "text/plain"}),
    }))
    saved = box.run("desk_fetch_file", url=url)
    box.ctx["tainted"] = False
    box.ctx["taint_sources"] = []
    copied = box.run("desk_write_file", path="work/copy.md", content=body.decode())
    assert box.ws.was_fetched(DESK, copied["path"])
    got = box.run("desk_read_file", path=copied["path"])
    assert "hello from the web" in got["text"]
    assert box.ctx["tainted"] is True and "desk_read_file" in box.ctx["taint_sources"]


def test_a_download_with_a_note_appended_stays_untrusted(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://example.com/long.txt"
    body = b"hello from the web. " + (b"sentence " * 12)
    assert len(body) >= 80
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open({
        url: httpx.Response(200, content=body, headers={"content-type": "text/plain"}),
    }))
    box.run("desk_fetch_file", url=url)
    box.ctx["tainted"] = False
    box.ctx["taint_sources"] = []
    copied = box.run("desk_write_file", path="work/noted.md", content=body.decode() + "\nMy note\n")
    assert box.ws.was_fetched(DESK, copied["path"])
    got = box.run("desk_read_file", path="work/noted.md")
    assert "My note" in got["text"] and box.ctx["tainted"] is True
    box.ctx["tainted"] = False
    box.ctx["taint_sources"] = []
    box.run("desk_write_file", path="work/own.md", content="mine\n")
    box.run("desk_read_file", path="work/own.md")
    assert box.ctx["tainted"] is False


def test_writing_extracted_download_text_stays_untrusted(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    from personal_os import extract_text as xt

    url = "https://example.com/q.pdf"
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open({
        url: httpx.Response(200, content=b"%PDF-1.4 not a real document", headers={"content-type": "application/pdf"}),
    }))
    saved = box.run("desk_fetch_file", url=url)
    assert saved["path"].endswith(".pdf")
    monkeypatch.setattr(xt, "extract_text", lambda name, data: "quoted page\n")
    box.ctx["tainted"] = False
    box.ctx["taint_sources"] = []
    copied = box.run("desk_write_file", path="work/notes.md", content="quoted page\n")
    assert box.ws.was_fetched(DESK, copied["path"])
    got = box.run("desk_read_file", path=copied["path"])
    assert got["text"].startswith("quoted page") and box.ctx["tainted"] is True


def test_fs_copy_of_a_download_stays_untrusted(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://example.com/note.txt"
    body = b"hello from the web\n"
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open({
        url: httpx.Response(200, content=body, headers={"content-type": "text/plain"}),
    }))
    saved = box.run("desk_fetch_file", url=url)
    copied = box.run("fs_copy", src=saved["path"], dst="work/via-fs.txt")
    assert copied["path"].endswith("via-fs.txt")
    assert box.ws.was_fetched(DESK, "work/via-fs.txt")
    box.ctx["tainted"] = False
    box.ctx["taint_sources"] = []
    got = box.run("desk_read_file", path="work/via-fs.txt")
    assert "hello from the web" in got["text"] and box.ctx["tainted"] is True
    own = box.run("desk_write_file", path="work/own.md", content="mine\n")
    again = box.run("fs_copy", src=own["path"], dst="work/own-copy.md")
    assert again["path"].endswith("own-copy.md")
    assert box.ws.was_fetched(DESK, "work/own-copy.md") is False


def test_a_script_copy_of_a_download_stays_untrusted(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://example.com/note.txt"
    body = b"hello from the web\n"
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open({
        url: httpx.Response(200, content=body, headers={"content-type": "text/plain"}),
    }))
    saved = box.run("desk_fetch_file", url=url)
    script = f"import shutil\nshutil.copy({saved['path']!r}, 'work/via-script.txt')\n"
    ran = box.run("run_python", code=script)
    assert ran.get("exit_code") == 0, ran
    assert box.ws.was_fetched(DESK, "work/via-script.txt")
    box.ctx["tainted"] = False
    box.ctx["taint_sources"] = []
    got = box.run("desk_read_file", path="work/via-script.txt")
    assert "hello from the web" in got["text"] and box.ctx["tainted"] is True


def test_a_shell_copy_of_a_download_stays_untrusted(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://example.com/note.txt"
    body = b"hello from the web\n"
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open({
        url: httpx.Response(200, content=body, headers={"content-type": "text/plain"}),
    }))
    saved = box.run("desk_fetch_file", url=url)
    ran = box.run("shell_run", command=f"cp {saved['path']} work/via-shell.txt", cwd=str(box.root))
    assert ran.get("exit_code") == 0, ran
    assert box.ws.was_fetched(DESK, "work/via-shell.txt")
    box.ctx["tainted"] = False
    box.ctx["taint_sources"] = []
    got = box.run("desk_read_file", path="work/via-shell.txt")
    assert "hello from the web" in got["text"] and box.ctx["tainted"] is True
    secret = Path(os.environ["PERSONAL_OS_DATA_DIR"]) / "personal-os.db"
    secret.write_bytes(b"not-for-the-shell")
    denied = box.run("shell_run", command=f"cat {secret}", cwd=str(box.root))
    assert denied.get("exit_code") != 0
    assert "not-for-the-shell" not in (denied.get("output") or "")


def test_renaming_a_download_keeps_it_untrusted(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://example.com/note.txt"
    body = b"hello from the web\n"
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open({
        url: httpx.Response(200, content=body, headers={"content-type": "text/plain"}),
    }))
    saved = box.run("desk_fetch_file", url=url)
    ran = box.run("shell_run", command=f"mv {saved['path']} work/renamed.txt", cwd=str(box.root))
    assert ran.get("exit_code") == 0, ran
    assert not (box.root / saved["path"]).exists()
    assert box.ws.was_fetched(DESK, "work/renamed.txt")
    box.ctx["tainted"] = False
    box.ctx["taint_sources"] = []
    got = box.run("desk_read_file", path="work/renamed.txt")
    assert "hello from the web" in got["text"] and box.ctx["tainted"] is True


def test_a_script_rename_of_a_download_stays_untrusted(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://example.com/note.txt"
    body = b"hello from the web\n"
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open({
        url: httpx.Response(200, content=body, headers={"content-type": "text/plain"}),
    }))
    saved = box.run("desk_fetch_file", url=url)
    script = f"import os\nos.rename({saved['path']!r}, 'work/renamed-py.txt')\n"
    ran = box.run("run_python", code=script)
    assert ran.get("exit_code") == 0, ran
    assert box.ws.was_fetched(DESK, "work/renamed-py.txt")
    box.ctx["tainted"] = False
    box.ctx["taint_sources"] = []
    got = box.run("desk_read_file", path="work/renamed-py.txt")
    assert "hello from the web" in got["text"] and box.ctx["tainted"] is True


def test_fetch_saves_with_hash_and_safe_name(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    body = b"%PDF-1.4 hello"
    url = "https://example.com/files/Q3%20report.pdf"
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open({url: httpx.Response(200, content=body, headers={"content-type": "application/pdf"})}))
    r = box.run("desk_fetch_file", url=url)
    assert r["path"] == "work/downloads/Q3 report.pdf" and r["bytes"] == len(body)
    assert r["sha256"] == hashlib.sha256(body).hexdigest() and r["content_type"] == "application/pdf"
    assert "desk_read_file" in r["hint"]
    assert (box.root / r["path"]).read_bytes() == body
    assert not list((box.root / "work" / "downloads").glob(".*.part"))
    # the same URL again never overwrites
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open({url: httpx.Response(200, content=b"two")}))
    assert box.run("desk_fetch_file", url=url)["path"] == "work/downloads/Q3 report 2.pdf"
    assert (box.root / r["path"]).read_bytes() == body


def test_fetch_content_disposition_and_picture_hint(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://example.com/dl?id=1"
    hdr = {"content-disposition": 'attachment; filename="../../evil name.png"', "content-type": "image/png"}
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open({url: httpx.Response(200, content=b"\x89PNG", headers=hdr)}))
    r = box.run("desk_fetch_file", url=url)
    assert r["path"] == "work/downloads/evil name.png" and "view_image" in r["hint"]
    r2 = box.run("desk_fetch_file", url=url, path="outputs/pic.png")
    assert r2["path"] == "outputs/pic.png"


def test_fetch_enforces_blocked_suffix_quota_and_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    b = Box(tmp_path, max_total_bytes=1000)
    url = "https://example.com/a.bin"
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open({url: httpx.Response(200, content=b"x" * 2000)}))
    over = b.run("desk_fetch_file", url=url)
    assert "remaining space" in over["error"] and not list(b.root.rglob("*.bin")) and not list(b.root.rglob("*.part"))
    blocked = b.run("desk_fetch_file", url=url, path="work/run.command")
    assert ".command" in blocked["error"]
    escape = b.run("desk_fetch_file", url=url, path="../outside.bin")
    assert escape["error"]
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open({url: httpx.Response(200, headers={"content-length": "60000000"}, content=b"x")}))
    assert "limit" in Box(tmp_path / "second").run("desk_fetch_file", url=url)["error"]


def test_fetch_checks_every_redirect_hop(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    start = "https://example.com/go"
    seen: list[str] = []
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open(
        {start: httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})}, seen))
    r = box.run("desk_fetch_file", url=start)
    assert "refused" in r["error"] and seen == [start]


def test_fetch_follows_a_safe_redirect_and_reports_http_errors(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    a, c = "https://example.com/a", "https://cdn.example.com/c.csv"
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open(
        {a: httpx.Response(301, headers={"location": c}), c: httpx.Response(200, content=b"a,b\n1,2\n")}))
    r = box.run("desk_fetch_file", url=a)
    assert r["path"] == "work/downloads/c.csv" and r["redirects"] == 1
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open({a: httpx.Response(404, content=b"nope")}))
    assert "404" in box.run("desk_fetch_file", url=a)["error"]


def test_fetch_obeys_the_tainted_run_rule(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    box.keep_taint = True
    box.ctx["tainted"] = True
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open({}))
    r = box.run("desk_fetch_file", url="https://example.com/x.pdf")
    assert "refused" in r["error"]
    spec = box.tb.specs["desk_fetch_file"]
    assert (spec.group, spec.danger, spec.taints) == ("desk", "network", True)
    box.keep_taint, box.ctx["tainted"] = False, False
    monkeypatch.setattr(tools_mod, "_open_pinned_stream", fake_open({"https://example.com/x.pdf": httpx.Response(200, content=b"%PDF")}))
    assert box.run("desk_fetch_file", url="https://example.com/x.pdf")["path"].endswith("x.pdf")
    assert box.ctx["tainted"] is True  # the download is third-party content


def test_a_networked_sandbox_import_stays_untrusted_after_clear(tmp_path: Path) -> None:
    class Sb:
        def __init__(self, net: bool) -> None:
            self.net = net

        def read_file(self, cid: str, path: str, off: int, length: int) -> dict[str, str]:
            return {"text": "from the net\n", "path": path}

        def networked(self, cid: str) -> bool:
            return self.net

        def holds_import(self, cid: str) -> bool:
            return False

    dirty = Box(tmp_path / "dirty", sandboxes=Sb(True))
    out = dirty.run("desk_import_sandbox", sandbox_path="out.md", path="work/out.md")
    assert out["path"] == "work/out.md" and dirty.ws.was_fetched(DESK, "work/out.md")
    dirty.ctx["tainted"] = False
    dirty.ctx["taint_sources"] = []
    got = dirty.run("desk_read_file", path="work/out.md")
    assert "from the net" in got["text"] and dirty.ctx["tainted"] is True

    clean = Box(tmp_path / "clean", sandboxes=Sb(False))
    clean.run("desk_import_sandbox", sandbox_path="out.md", path="work/out.md")
    assert clean.ws.was_fetched(DESK, "work/out.md") is False
    clean.ctx["tainted"] = False
    clean.run("desk_read_file", path="work/out.md")
    assert clean.ctx["tainted"] is False


def test_import_sandbox_description_is_truthful(box: Box) -> None:
    d = box.tb.specs["desk_import_sandbox"].description
    assert "share no directory" not in d and "/workspace/desk" in d
