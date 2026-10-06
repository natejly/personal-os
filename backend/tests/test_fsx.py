"""File tools over granted folders (fsx.py): glob, grep, exact-string edit, copy, mkdir, the read-before-write
ledger, secret-file refusals, and the desk mount in the sandbox container's argv. Offline: a fake HOME in a tmpdir."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import fsx, mac  # noqa: E402
from personal_os.microvm import DESK_MOUNT, Sandboxes  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402
from personal_os.workspace import Workspace  # noqa: E402


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = (tmp_path / "home").resolve()
    (h / "proj").mkdir(parents=True)
    (h / "other").mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def make(home: Path, tmp_path: Path, **settings: Any) -> tuple[Toolbox, dict[str, Any]]:
    cfg: dict[str, Any] = {"workspaceRoots": [str(home / "proj")], **settings}
    ws = Workspace(tmp_path / "data")
    tb = Toolbox(None, None, None, lambda: cfg, workspace=ws)  # type: ignore[arg-type]
    return tb, cfg


def call(tb: Toolbox, name: str, ctx: dict[str, Any] | None = None, **args: Any) -> Any:
    ctx = ctx if ctx is not None else {"conversation_id": "c1"}
    out = asyncio.run(tb.call(name, args, ctx))
    # reading the user's own files taints the ctx (taints=True) but is local-only: the app loop records the source
    if ctx.get("tainted") and not ctx.get("taint_sources"):
        ctx["taint_sources"] = ["read_local_file"]
    return out


def test_registered_with_expected_tiers(home: Path, tmp_path: Path) -> None:
    tb, _ = make(home, tmp_path)
    modes = tb.effective({}, None, None)
    for n in ("fs_glob", "fs_grep", "fs_edit", "fs_copy", "fs_mkdir"):
        assert modes[n] == "on" and tb.specs[n].group == "files", n
    assert tb.specs["fs_glob"].danger == "safe" or tb.specs["fs_glob"].danger == "safe"


def test_glob_skips_and_caps(home: Path, tmp_path: Path) -> None:
    tb, _ = make(home, tmp_path)
    p = home / "proj"
    (p / "src").mkdir()
    (p / "node_modules" / "x").mkdir(parents=True)
    (p / ".git").mkdir()
    (p / "src" / "a.py").write_text("a")
    (p / "node_modules" / "x" / "b.py").write_text("b")
    (p / ".git" / "c.py").write_text("c")
    (p / ".env").write_text("SECRET=1")
    out = call(tb, "fs_glob", pattern="**/*.py", root=str(p))
    assert [r["rel"] for r in out["files"]] == ["src/a.py"]
    assert not call(tb, "fs_glob", pattern="*", root=str(p))["files"] == [] and all(".env" not in r["rel"] for r in call(tb, "fs_glob", pattern="*", root=str(p))["files"])
    for i in range(520):
        (p / f"f{i}.txt").write_text("x")
    big = call(tb, "fs_glob", pattern="*.txt", root=str(p))
    assert len(big["files"]) == 500 and big["total"] == 520 and big["truncated"]
    # newest first
    os.utime(p / "f3.txt", (1, 1))
    os.utime(p / "f4.txt", (9_999_999_999, 9_999_999_999))
    rows = call(tb, "fs_glob", pattern="*.txt", root=str(p))["files"]
    assert rows[0]["rel"] == "f4.txt" and "f3.txt" not in [r["rel"] for r in rows]  # oldest falls off the cap
    assert call(tb, "fs_glob", pattern="../*", root=str(p))["error"]


def test_a_token_in_a_glob_path_is_stripped(home: Path, tmp_path: Path) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    tb, _ = make(home, tmp_path)
    p = home / "proj"
    (p / f"note-{pat}.txt").write_text("x")
    out = call(tb, "fs_glob", pattern="note-*.txt", root=str(p))
    assert out["total"] == 1
    row = out["files"][0]
    assert pat not in row["path"] and pat not in row["rel"]
    assert "[github-pat]" in row["path"] and "[github-pat]" in row["rel"]
    assert (p / f"note-{pat}.txt").is_file()


def test_a_token_in_a_grep_path_is_stripped(home: Path, tmp_path: Path) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    tb, _ = make(home, tmp_path)
    p = home / "proj"
    (p / f"note-{pat}.txt").write_text("needle\n")
    out = call(tb, "fs_grep", {"conversation_id": "c1"}, pattern="needle", root=str(p))
    assert out["count"] == 1
    shown = out["matches"][0]["path"]
    assert pat not in shown and "[github-pat]" in shown
    assert (p / f"note-{pat}.txt").is_file()


def _grep_fixture(p: Path) -> None:
    (p / "a.py").write_text("one\nTODO fix this\nthree\n")
    (p / "b.md").write_text("TODO docs\n")
    (p / "bin.dat").write_bytes(b"TODO\x00\x01")
    (p / ".env").write_text("TODO=secret\n")
    (p / "long.txt").write_text("TODO " + "x" * 1000 + "\n")


@pytest.mark.parametrize("use_rg", [False, True])
def test_grep_with_and_without_rg(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, use_rg: bool) -> None:
    if use_rg:
        if not shutil.which("rg") or subprocess.run(["rg", "--version"], capture_output=True).returncode != 0:
            pytest.skip("no rg binary on PATH")
    else:
        monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    tb, _ = make(home, tmp_path)
    p = home / "proj"
    _grep_fixture(p)
    out = call(tb, "fs_grep", pattern="TODO", root=str(p))
    paths = sorted(Path(m["path"]).name for m in out["matches"])
    assert paths == ["a.py", "b.md", "long.txt"], paths  # binary and .env skipped
    assert all(len(m["text"]) <= 400 for m in out["matches"])
    ctx = call(tb, "fs_grep", pattern="TODO fix", root=str(p), glob="*.py", context=1)
    assert [m["line"] for m in ctx["matches"]] == [1, 2, 3] and ctx["count"] == 1
    assert call(tb, "fs_grep", pattern="(", root=str(p))["error"]
    for i in range(300):
        (p / f"m{i}.txt").write_text("needle\n")
    capped = call(tb, "fs_grep", pattern="needle", root=str(p))
    assert capped["count"] == 200 and capped["truncated"]


def test_edit_exact_diff_and_errors(home: Path, tmp_path: Path) -> None:
    tb, _ = make(home, tmp_path)
    f = home / "proj" / "m.py"
    f.write_text("a = 1\nb = 2\nb = 2\n")
    ctx = {"conversation_id": "c1"}
    unread = call(tb, "fs_edit", ctx, path=str(f), old="a = 1", new="a = 9")
    assert "not been read" in unread["error"]
    call(tb, "read_local_file", ctx, path=str(f))
    zero = call(tb, "fs_edit", ctx, path=str(f), old="zzz", new="y")
    assert "not found" in zero["error"]
    many = call(tb, "fs_edit", ctx, path=str(f), old="b = 2", new="b = 3")
    assert "2 places" in many["error"] and f.read_text() == "a = 1\nb = 2\nb = 2\n"
    one = call(tb, "fs_edit", ctx, path=str(f), old="a = 1", new="a = 9")
    assert one["matcher"] == "exact" and one["replacements"] == 1 and "-a = 1" in one["diff"] and "+a = 9" in one["diff"]
    assert f.read_text() == "a = 9\nb = 2\nb = 2\n"
    allr = call(tb, "fs_edit", ctx, path=str(f), old="b = 2", new="b = 3", replace_all=True)
    assert allr["replacements"] == 2 and f.read_text() == "a = 9\nb = 3\nb = 3\n"


def test_a_token_in_an_edit_diff_is_stripped(home: Path, tmp_path: Path) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    tb, _ = make(home, tmp_path)
    f = home / "proj" / f"m-{pat}.py"
    f.write_text(f"token = {pat!r}\n")
    ctx = {"conversation_id": "c1"}
    call(tb, "read_local_file", ctx, path=str(f))
    out = call(tb, "fs_edit", ctx, path=str(f), old=f"token = {pat!r}", new='token = "ok"')
    assert pat not in out["path"] and pat not in out["diff"]
    assert "[github-pat]" in out["path"] and "[github-pat]" in out["diff"]
    assert pat in f.name and 'token = "ok"' in f.read_text()


def test_edit_fuzzy_matchers() -> None:
    text = "def f():\n    x = 1\n    y = 2\n    return x + y\n"
    out, m, n, _, _ = fsx.replace_text(text, "x = 1\ny = 2", "x = 10\ny = 20")
    assert m == "line-trimmed" and out == "def f():\n    x = 10\n    y = 20\n    return x + y\n"  # indentation follows the file
    # block anchor: first and last lines exact, middle drifted but similar
    old = "def f():\n    x = 1\n    y = 3\n    return x + y\n"
    out, m, n, _, _ = fsx.replace_text(text, old, "def f():\n    return 0\n")
    assert m == "block-anchor" and out == "def f():\n    return 0\n"
    # a middle that is nothing like it does not match
    with pytest.raises(fsx.EditError):
        fsx.replace_text(text, "def f():\n    totally\n    different\n    return x + y\n", "q")
    # several line-trimmed matches still error
    dup = "  a\n  b\n  a\n  b\n"
    with pytest.raises(fsx.EditError):
        fsx.replace_text(dup, "a\nb", "c")
    out, m, n, _, _ = fsx.replace_text(dup, "a\nb", "c", replace_all=True)
    assert n == 2 and out == "  c\n  c\n"
    # CRLF files keep their line endings
    crlf = "one\r\ntwo\r\n"
    out, m, n, _, _ = fsx.replace_text(crlf, " one\ntwo", "1\n2")
    assert out == "1\r\n2\r\n"


def test_read_before_write_ledger(home: Path, tmp_path: Path) -> None:
    tb, _ = make(home, tmp_path)
    f = home / "other" / "n.txt"
    f.write_text("0123456789\n" * 400)  # ~4.4k chars
    ctx = {"conversation_id": "c1"}
    # an overwrite of an unread file is refused
    r = call(tb, "write_local_file", ctx, path=str(f), content="new", mode="overwrite")
    assert "has not been read" in r["error"]
    # a partial read is not enough for an overwrite
    call(tb, "read_local_file", ctx, path=str(f), offset=0, length=100)
    assert "partly read" in call(tb, "write_local_file", ctx, path=str(f), content="new", mode="overwrite")["error"]
    # ...but covers an edit inside it
    call(tb, "fs_edit", ctx, path=str(f), old="0123456789", new="abcdefghij")  # outside the grant: refused before the ledger
    # a full read opens the overwrite
    call(tb, "read_local_file", ctx, path=str(f), offset=0, length=30000)
    assert call(tb, "write_local_file", ctx, path=str(f), content="new", mode="overwrite").get("chars_written") == 3
    # another conversation has read nothing
    other = {"conversation_id": "c2"}
    assert "has not been read" in call(tb, "write_local_file", other, path=str(f), content="x", mode="overwrite")["error"]
    # a change behind the agent's back invalidates the baseline
    st = f.stat()
    f.write_text("someone else")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))
    assert "changed since" in call(tb, "write_local_file", ctx, path=str(f), content="y", mode="overwrite")["error"]
    # create and append never need a read
    assert call(tb, "write_local_file", ctx, path=str(home / "other" / "fresh.txt"), content="hi")["created"]
    assert call(tb, "write_local_file", other, path=str(f), content="more", mode="append")["chars_written"] == 4


def test_partial_read_covers_only_its_region(home: Path, tmp_path: Path) -> None:
    tb, _ = make(home, tmp_path)
    f = home / "proj" / "big.txt"
    f.write_text("".join(f"line {i:03d}\n" for i in range(200)))
    ctx = {"conversation_id": "c1"}
    call(tb, "read_local_file", ctx, path=str(f), offset=0, length=100)
    assert call(tb, "fs_edit", ctx, path=str(f), old="line 001", new="LINE 001")["replacements"] == 1
    far = call(tb, "fs_edit", ctx, path=str(f), old="line 190", new="LINE 190")
    assert "not in what you read" in far["error"]
    g = call(tb, "fs_grep", ctx, pattern="line 190", root=str(f))  # a grep hit counts as reading that line
    assert g["count"] == 1
    assert call(tb, "fs_edit", ctx, path=str(f), old="line 190", new="LINE 190")["replacements"] == 1


def test_repeated_identical_reads(home: Path, tmp_path: Path) -> None:
    tb, _ = make(home, tmp_path)
    f = home / "other" / "r.txt"
    f.write_text("same")
    ctx = {"conversation_id": "c1"}
    a = call(tb, "read_local_file", ctx, path=str(f))
    b = call(tb, "read_local_file", ctx, path=str(f))
    assert a["text"] == b["text"] == "same"
    stub = call(tb, "read_local_file", ctx, path=str(f))
    assert stub.get("unchanged") and "text" not in stub
    assert call(tb, "read_local_file", ctx, path=str(f))["error"]
    f.write_text("changed")
    os.utime(f, ns=(1, f.stat().st_mtime_ns + 7_000_000))
    assert call(tb, "read_local_file", ctx, path=str(f))["text"] == "changed"


def test_secret_files_are_refused_even_through_symlinks(home: Path, tmp_path: Path) -> None:
    tb, _ = make(home, tmp_path)
    p = home / "proj"
    (p / "app.env").write_text("x")
    secret = home / "other" / ".env"
    secret.write_text("TOKEN=abc")
    (p / "notes.txt").symlink_to(secret)
    (p / "ssh").mkdir()
    (p / "id_rsa").write_text("-----BEGIN")
    (p / "cert.pem").write_text("x")
    ctx = {"conversation_id": "c1"}
    for name in ("notes.txt", "id_rsa", "cert.pem"):
        assert "error" in call(tb, "read_local_file", ctx, path=str(p / name)), name
    # a symlinked ROOT pointing at a secret folder is refused too
    sshdir = home / ".ssh"
    sshdir.mkdir()
    (sshdir / "config").write_text("Host x")
    (home / "link").symlink_to(sshdir)
    assert "error" in call(tb, "read_local_file", ctx, path=str(home / "link" / "config"))
    out = call(tb, "fs_grep", ctx, pattern="TOKEN|BEGIN|Host", root=str(p))
    assert out["count"] == 0
    assert fsx.sensitive_reason("/dev/null") and fsx.sensitive_reason("/proc/1/environ")
    assert fsx.sensitive_reason("/x/.env.local") and not fsx.sensitive_reason("/x/key_notes.md")


def test_writes_outside_the_grant_need_approval(home: Path, tmp_path: Path) -> None:
    tb, _ = make(home, tmp_path)
    inside, outside = home / "proj" / "ok.txt", home / "other" / "no.txt"
    inside.write_text("a")
    outside.write_text("a")
    ctx = {"conversation_id": "c1"}
    call(tb, "read_local_file", ctx, path=str(inside))
    call(tb, "read_local_file", ctx, path=str(outside))
    # the reply loop asks about exactly the calls the tool would refuse
    assert not tb.fs_needs_ask("fs_edit", {"path": str(inside), "old": "a", "new": "b"}, ctx)
    assert tb.fs_needs_ask("fs_edit", {"path": str(outside), "old": "a", "new": "b"}, ctx)
    assert tb.fs_needs_ask("fs_mkdir", {"path": str(home / "other" / "d")}, ctx)
    assert tb.fs_needs_ask("fs_copy", {"src": str(inside), "dst": str(home / "other" / "c.txt")}, ctx)
    assert not tb.fs_needs_ask("fs_glob", {"pattern": "*"}, ctx)
    refused = call(tb, "fs_edit", ctx, path=str(outside), old="a", new="b")
    assert "needs their approval" in refused["error"] and outside.read_text() == "a"
    assert not (home / "other" / "d").exists() or call(tb, "fs_mkdir", ctx, path=str(home / "other" / "d"))["error"]
    # once the loop has the user's yes it sets the flag, and the same call goes through
    ok = call(tb, "fs_edit", {**ctx, "fs_outside_ok": True}, path=str(outside), old="a", new="b")
    assert ok["replacements"] == 1 and outside.read_text() == "b"
    # untrusted content in the reply makes even a granted folder ask
    assert tb.fs_needs_ask("fs_edit", {"path": str(inside), "old": "a", "new": "b"}, {**ctx, "tainted": True, "taint_sources": ["fetch_url"]})
    # an unattended run may not write outside a desk workspace even with the flag
    bg = call(tb, "fs_mkdir", {**ctx, "proposal_only": True, "fs_outside_ok": True}, path=str(home / "proj" / "x"))
    assert "unattended" in bg["error"]


def test_symlink_out_of_a_root_is_outside(home: Path, tmp_path: Path) -> None:
    tb, _ = make(home, tmp_path)
    target = home / "other" / "t.txt"
    target.write_text("a")
    link = home / "proj" / "link.txt"
    link.symlink_to(target)
    ctx = {"conversation_id": "c1"}
    assert tb.fs_needs_ask("fs_edit", {"path": str(link), "old": "a", "new": "b"}, ctx)
    call(tb, "read_local_file", ctx, path=str(link))
    assert "needs their approval" in call(tb, "fs_edit", ctx, path=str(link), old="a", new="b")["error"]
    assert target.read_text() == "a"
    assert not mac.in_roots(link, [home / "proj"]) and mac.in_roots(home / "proj" / "x", [home / "proj"])


def test_a_token_in_a_file_tool_error_is_stripped(home: Path, tmp_path: Path) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    tb, _ = make(home, tmp_path)
    p = home / "proj"
    missing = p / f"missing-{pat}.txt"
    out = call(tb, "fs_copy", {"conversation_id": "c1"}, src=str(missing), dst=str(p / "out.txt"))
    assert "error" in out
    assert pat not in out["error"] and "[github-pat]" in out["error"]
    assert not missing.exists()


def test_a_token_in_a_repeated_read_path_is_stripped(home: Path, tmp_path: Path) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    tb, _ = make(home, tmp_path)
    f = home / "proj" / f"note-{pat}.txt"
    f.write_text("hello\n")
    ctx = {"conversation_id": "c1"}
    for _ in range(2):
        call(tb, "read_local_file", ctx, path=str(f))
    stub = call(tb, "read_local_file", ctx, path=str(f))
    assert stub.get("unchanged") is True
    assert pat not in stub["path"] and "[github-pat]" in stub["path"]
    refused = call(tb, "read_local_file", ctx, path=str(f))
    assert pat not in refused["error"] and "[github-pat]" in refused["error"]
    assert f.read_text() == "hello\n"


def test_a_token_in_a_copied_path_is_stripped(home: Path, tmp_path: Path) -> None:
    pat = "github_pat_11AAAAAAA0AAAAAAAAAAAAAAAAAAAA"
    tb, _ = make(home, tmp_path)
    p = home / "proj"
    (p / "a.txt").write_text("A")
    ctx = {"conversation_id": "c1"}
    copied = call(tb, "fs_copy", ctx, src=str(p / "a.txt"), dst=str(p / f"b-{pat}.txt"))
    assert pat not in copied["path"] and "[github-pat]" in copied["path"]
    assert (p / f"b-{pat}.txt").read_text() == "A"
    made = call(tb, "fs_mkdir", ctx, path=str(p / f"dir-{pat}"))
    assert pat not in made["path"] and "[github-pat]" in made["path"]
    assert (p / f"dir-{pat}").is_dir()


def test_copy_and_mkdir(home: Path, tmp_path: Path) -> None:
    tb, _ = make(home, tmp_path)
    p = home / "proj"
    (p / "a.txt").write_text("A")
    ctx = {"conversation_id": "c1"}
    out = call(tb, "fs_copy", ctx, src=str(p / "a.txt"), dst=str(p / "b.txt"))
    assert out["files"] == 1 and (p / "b.txt").read_text() == "A"
    again = call(tb, "fs_copy", ctx, src=str(p / "a.txt"), dst=str(p / "b.txt"))
    assert "already exists" in again["error"] and (p / "b.txt").read_text() == "A"
    (p / "d").mkdir()
    into = call(tb, "fs_copy", ctx, src=str(p / "a.txt"), dst=str(p / "d"))  # a folder receives it under the same name
    assert Path(into["path"]) == p / "d" / "a.txt"
    assert call(tb, "fs_mkdir", ctx, path=str(p / "x" / "y" / "z"))["created"]
    assert (p / "x" / "y" / "z").is_dir()
    assert call(tb, "fs_mkdir", ctx, path=str(p / "x" / "y" / "z"))["created"] is False
    assert call(tb, "fs_mkdir", ctx, path=str(p / "a.txt"))["error"]
    assert call(tb, "fs_copy", ctx, src=str(p / "missing"), dst=str(p / "q"))["error"]


def test_folder_copy_reports_what_it_left_out_and_cleans_up_a_failure(home: Path, tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    tb, _ = make(home, tmp_path)
    src = home / "proj" / "site"
    (src / ".git").mkdir(parents=True)
    (src / "node_modules").mkdir()
    (src / "index.html").write_text("hi")
    (src / "more.txt").write_text("x")
    (src / "link").symlink_to(src / "index.html")
    ctx = {"conversation_id": "c1"}
    out = call(tb, "fs_copy", ctx, src=str(src), dst=str(home / "proj" / "copy"))
    assert out["files"] == 2 and out["skipped"] == 3
    assert set(out["skipped_sample"]) == {".git/", "node_modules/", "link"}
    monkeypatch.setattr(fsx, "COPY_MAX_FILES", 1)
    big = call(tb, "fs_copy", ctx, src=str(src), dst=str(home / "proj" / "copy2"))
    assert "larger than" in big["error"] and not (home / "proj" / "copy2").exists()  # no half-copied folder left


def test_syntax_check_after_write(home: Path, tmp_path: Path) -> None:
    tb, _ = make(home, tmp_path)
    f = home / "proj" / "s.py"
    f.write_text("x = 1\n")
    ctx = {"conversation_id": "c1"}
    call(tb, "read_local_file", ctx, path=str(f))
    bad = call(tb, "fs_edit", ctx, path=str(f), old="x = 1", new="x = (")
    assert "SyntaxError" in bad["syntax_error"] and f.read_text() == "x = (\n"
    j = home / "proj" / "c.json"
    w = call(tb, "write_local_file", ctx, path=str(j), content='{"a": ')
    assert "syntax_error" in w
    assert "syntax_error" not in call(tb, "write_local_file", ctx, path=str(home / "proj" / "ok.json"), content="{}")
    assert fsx.syntax_check("a.yaml", "a: [") is None or fsx.syntax_check("a.yaml", "a: [")
    assert fsx.syntax_check("a.toml", "x = 1") is None


def test_edit_snapshots_for_undo(home: Path, tmp_path: Path) -> None:
    from personal_os.db import Database
    from personal_os.filesnap import FileSnapshots

    db = Database(tmp_path / "db")
    fs = FileSnapshots(db, tmp_path / "snaps", lambda: {})
    cfg: dict[str, Any] = {"workspaceRoots": [str(home / "proj")]}
    tb = Toolbox(None, None, None, lambda: cfg, filesnap=fs)  # type: ignore[arg-type]
    f = home / "proj" / "u.txt"
    f.write_text("before")
    ctx = {"conversation_id": "c1"}
    call(tb, "read_local_file", ctx, path=str(f))
    out = call(tb, "fs_edit", ctx, path=str(f), old="before", new="after")
    sid = out["undo"]["snapshot_id"]
    assert sid and f.read_text() == "after"
    fs.restore(sid)
    assert f.read_text() == "before"


def test_desk_workspace_is_always_granted(home: Path, tmp_path: Path) -> None:
    tb, _ = make(home, tmp_path, workspaceRoots=[])
    root = tb.workspace.ensure("desk1")
    ctx = {"conversation_id": "c1", "desk_id": "desk1"}
    (root / "work" / "n.txt").write_text("hello")
    call(tb, "read_local_file", ctx, path=str(home / "proj"))  # unrelated read still fine
    assert not tb.fs_needs_ask("fs_mkdir", {"path": "work/sub"}, ctx)  # relative paths land in the desk
    assert call(tb, "fs_mkdir", ctx, path="work/sub")["created"]
    assert (root / "work" / "sub").is_dir()
    rows = call(tb, "fs_glob", ctx, pattern="work/*.txt")["files"]
    assert [r["rel"] for r in rows] == ["work/n.txt"]
    assert call(tb, "fs_grep", ctx, pattern="hell")["count"] == 1
    # bookkeeping folders are not a write target, and nothing escapes by '..'
    assert "bookkeeping" in call(tb, "fs_mkdir", ctx, path=".baseline/x")["error"]
    assert "error" in call(tb, "fs_mkdir", ctx, path="../escape")
    # a desk edit needs a read like any other
    call(tb, "fs_grep", ctx, pattern="hello")
    ed = call(tb, "fs_edit", ctx, path="work/n.txt", old="hello", new="bye")
    assert ed["replacements"] == 1 and (root / "work" / "n.txt").read_text() == "bye"


# ---- microvm: the desk mount
class Recorder:
    def __init__(self) -> None:
        self.argv: list[list[str]] = []

    def __call__(self, argv: list[str], *, input: bytes | None = None, timeout: float = 60) -> "subprocess.CompletedProcess[bytes]":
        self.argv.append(list(argv))
        if argv[1] == "inspect":
            return subprocess.CompletedProcess(argv, 1, b"", b"No such object")
        return subprocess.CompletedProcess(argv, 0, b"/usr/bin/bash", b"")


def test_docker_argv_mounts_only_an_active_desk(tmp_path: Path) -> None:
    cfg: dict[str, Any] = {}
    rec = Recorder()
    sb = Sandboxes(lambda: cfg, runner=rec)
    sb.desk_workspace = lambda conv: str(tmp_path / "cowork" / "d1") if conv == "desk-conv" else None
    sb.ensure("plain-conv")
    run = next(a for a in rec.argv if a[1] == "run")
    assert "-v" not in run
    rec.argv.clear()
    sb.ensure("desk-conv")
    run = next(a for a in rec.argv if a[1] == "run")
    assert run[run.index("-v") + 1] == f"{tmp_path / 'cowork' / 'd1'}:{DESK_MOUNT}:rw"
    assert run.count("-v") == 1
    # switched off by setting
    cfg["sandboxMountDesk"] = False
    rec.argv.clear()
    Sandboxes(lambda: cfg, runner=rec).ensure("x")
    assert "-v" not in next(a for a in rec.argv if a[1] == "run")
    sb2 = Sandboxes(lambda: cfg, runner=rec)
    sb2.desk_workspace = lambda conv: str(tmp_path)
    rec.argv.clear()
    sb2.ensure("desk-conv")
    assert "-v" not in next(a for a in rec.argv if a[1] == "run")


def test_rg_output_is_parsed_and_filtered(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    p = home / "proj"
    (p / "a.py").write_text("x\nTODO one\n")
    (p / ".env").write_text("TODO=1\n")

    def ev(kind: str, path: Path, n: int, text: str) -> bytes:
        return json.dumps({"type": kind, "data": {"path": {"text": str(path)}, "line_number": n, "lines": {"text": text}}}).encode()

    out = b"\n".join([ev("context", p / "a.py", 1, "x\n"), ev("match", p / "a.py", 2, "TODO one\n"),
                      ev("match", p / ".env", 1, "TODO=1\n"), json.dumps({"type": "summary"}).encode()])
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kw: Any) -> "subprocess.CompletedProcess[bytes]":
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, out, b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    rows, truncated = fsx.grep_rg("rg", p, "TODO", "*.py", 1, False)
    assert [(r["line"], bool(r.get("context"))) for r in rows] == [(1, True), (2, False)] and not truncated
    assert "--json" in seen[0] and "-C" in seen[0] and seen[0][-2:] == ["--", str(p)]
