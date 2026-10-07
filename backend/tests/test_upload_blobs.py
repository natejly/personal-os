"""Originals kept as uploads/<sha256>/<name>: storage, the legacy move, refcounted purge, and the raw/preview/open/reveal routes.

Run: cd backend && uv run pytest tests/test_upload_blobs.py -q -p no:cacheprovider -W ignore
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="blobs-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import blobs  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
UP = appmod.db.data_dir / "uploads"
sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731


def upload(name: str, data: bytes, mime: str = "application/pdf", project_id: str | None = None) -> dict[str, Any]:
    r = client.post("/documents", files={"file": (name, data, mime)}, data={"project_id": project_id} if project_id else {})
    assert r.status_code == 200, r.text
    return r.json()


def purge(did: str) -> None:
    assert client.delete(f"/documents/{did}").status_code == 200
    assert client.delete(f"/trash/document/{did}").status_code == 200


def legacy(name: str, data: bytes, text: str = "t") -> tuple[str, Path]:
    """A row as the old writers made it: uploads/<16 hex>-<name>, content_hash of whatever."""
    p = UP / f"0123456789abcdef-{name}"
    p.write_bytes(data)
    row = appmod.documents.create(None, name, "application/pdf", len(data), str(p), text)
    return row["id"], p


def row_path(did: str) -> str:
    with appmod.db.tx() as c:
        return c.execute("SELECT path FROM documents WHERE id=?", (did,)).fetchone()["path"]


def test_upload_writes_a_content_addressed_blob() -> None:
    data = b"%PDF-1 first"
    row = upload("Quarterly Report.pdf", data)
    assert row["path"] == str(UP / sha(data) / "Quarterly Report.pdf")
    assert Path(row["path"]).read_bytes() == data and row["has_original"] is True
    assert client.get(f"/documents/{row['id']}").json()["has_original"] is True


def test_same_bytes_in_two_projects_share_one_file() -> None:
    data = b"%PDF-1 shared"
    a, b = appmod.projects.create("A")["id"], appmod.projects.create("B")["id"]
    r1, r2 = upload("x.pdf", data, project_id=a), upload("renamed.pdf", data, project_id=b)
    assert r1["id"] != r2["id"] and r1["path"] == r2["path"]
    assert [f.name for f in (UP / sha(data)).iterdir()] == ["x.pdf"]


def test_purge_is_refcounted_and_trash_still_holds_the_blob() -> None:
    data = b"%PDF-1 refcount"
    a, b = appmod.projects.create("A")["id"], appmod.projects.create("B")["id"]
    r1, r2 = upload("x.pdf", data, project_id=a), upload("x.pdf", data, project_id=b)
    f = Path(r1["path"])
    purge(r1["id"])
    assert f.is_file()
    assert client.delete(f"/documents/{r2['id']}").status_code == 200  # trashed, not purged
    appmod.trash._purge_documents(["no-such-row"])
    assert f.is_file()
    assert client.delete(f"/trash/document/{r2['id']}").status_code == 200
    assert not f.exists() and not f.parent.exists()


def test_failed_create_leaves_no_blob(monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"%PDF-1 boom"

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(appmod.documents, "create", boom)
    assert client.post("/documents", files={"file": ("a.pdf", data, "application/pdf")}).status_code == 400
    assert not (UP / sha(data)).exists()


# ---- legacy move ----
def test_migrate_moves_a_legacy_file_and_is_idempotent() -> None:
    data = b"%PDF-1 legacy"
    did, old = legacy("old.pdf", data)
    blobs.migrate(appmod.db)
    new = UP / sha(data) / "old.pdf"
    assert row_path(did) == str(new) and new.read_bytes() == data and not old.exists()
    with appmod.db.tx() as c:
        assert c.execute("SELECT content_hash FROM documents WHERE id=?", (did,)).fetchone()[0] == sha(data)
    blobs.migrate(appmod.db)
    assert row_path(did) == str(new) and new.is_file()
    assert appmod.documents.get(did)["has_original"] is True


def test_migrate_skips_a_missing_file_and_untouched_rows() -> None:
    row = appmod.documents.create(None, "gone.pdf", "application/pdf", 3, str(UP / "0123456789abcdef-gone.pdf"), "t")
    done = upload("done.pdf", b"%PDF-1 done")
    blobs.migrate(appmod.db)
    assert row_path(row["id"]) == str(UP / "0123456789abcdef-gone.pdf")
    assert appmod.documents.get(row["id"])["has_original"] is False
    assert row_path(done["id"]) == done["path"]


def test_migrate_converges_after_an_interrupted_run() -> None:
    data = b"%PDF-1 interrupted"
    did, old = legacy("half.pdf", data)
    d = UP / sha(data)
    d.mkdir()
    os.link(old, d / "half.pdf")  # crashed after the link, before the row update
    blobs.migrate(appmod.db)
    assert row_path(did) == str(d / "half.pdf") and not old.exists() and (d / "half.pdf").read_bytes() == data


def test_migrate_never_deletes_when_the_target_hash_differs() -> None:
    data = b"%PDF-1 mismatch"
    did, old = legacy("m.pdf", data)
    d = UP / sha(data)
    d.mkdir()
    (d / "m.pdf").write_bytes(b"something else")
    blobs.migrate(appmod.db)
    assert row_path(did) == str(old) and old.read_bytes() == data and (d / "m.pdf").read_bytes() == b"something else"


# ---- raw ----
def test_raw_serves_the_original() -> None:
    data = b"%PDF-1 raw " + b"x" * 50
    row = upload("Résumé v2.pdf", data)
    r = client.get(f"/documents/{row['id']}/raw")
    assert r.status_code == 200 and r.content == data and r.headers["content-type"] == "application/pdf"
    assert r.headers["x-content-type-options"] == "nosniff"
    cd = r.headers["content-disposition"]
    assert cd == 'inline; filename="R_sum_ v2.pdf"; filename*=UTF-8\'\'R%C3%A9sum%C3%A9%20v2.pdf', cd
    part = client.get(f"/documents/{row['id']}/raw", headers={"Range": "bytes=0-3"})
    assert part.status_code == 206 and part.content == data[:4]


def test_raw_html_is_served_as_text() -> None:
    row = upload("page.html", b"<script>alert(1)</script>", mime="text/html; charset=utf-8")
    assert client.get(f"/documents/{row['id']}/raw").headers["content-type"].startswith("text/plain")


def test_raw_404s() -> None:
    row = upload("t.pdf", b"%PDF-1 trashed")
    assert client.delete(f"/documents/{row['id']}").status_code == 200
    assert client.get(f"/documents/{row['id']}/raw").status_code == 404
    gone = appmod.documents.create(None, "g.pdf", "application/pdf", 1, str(UP / "nope.pdf"), "t")
    assert client.get(f"/documents/{gone['id']}/raw").status_code == 404
    outside = Path(tempfile.mkdtemp()) / "secret.txt"
    outside.write_text("secret")
    out = appmod.documents.create(None, "s.txt", "text/plain", 6, str(outside), "t")
    assert out["has_original"] is False
    assert client.get(f"/documents/{out['id']}/raw").status_code == 404
    assert client.post(f"/documents/{out['id']}/open").status_code == 404
    assert client.post(f"/documents/{out['id']}/reveal").status_code == 404


# ---- preview ----
def test_preview_is_markdown_for_non_word_files(monkeypatch: pytest.MonkeyPatch) -> None:
    row = upload("notes.txt", b"hello preview", mime="text/plain")
    assert client.get(f"/documents/{row['id']}/preview").json() == {"kind": "markdown", "text": "hello preview"}
    ran: list[Any] = []
    monkeypatch.setattr(appmod.subprocess, "run", lambda *a, **k: ran.append(a))
    row = upload("mystery.bin", b"\x00\x01 unknown", mime="application/octet-stream")
    assert client.get(f"/documents/{row['id']}/preview").json()["kind"] == "markdown" and not ran
    assert client.get("/documents/nope/preview").status_code == 404


@pytest.mark.skipif(shutil.which("textutil") is None, reason="textutil is macOS only")
def test_preview_docx_is_sanitised_html(tmp_path: Path) -> None:
    src = tmp_path / "in.html"
    src.write_text("<html><body><p onclick='x()'>Hello <b>docx</b></p><script>bad()</script></body></html>")
    subprocess.run(["textutil", "-convert", "docx", str(src), "-output", str(tmp_path / "in.docx")], check=True)
    row = upload("letter.docx", (tmp_path / "in.docx").read_bytes(), mime="application/octet-stream")
    out = client.get(f"/documents/{row['id']}/preview").json()
    assert out["kind"] == "html" and "docx" in out["html"] and "<script" not in out["html"] and "onclick" not in out["html"]


def test_preview_falls_back_when_textutil_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*a: Any, **k: Any) -> Any:
        raise FileNotFoundError("textutil")

    monkeypatch.setattr(appmod.subprocess, "run", fail)
    appmod._office_html.cache_clear()
    row = upload("broken.docx", b"PK not really a docx", mime="application/octet-stream")
    out = client.get(f"/documents/{row['id']}/preview").json()
    assert out["kind"] == "markdown" and out["text"] == client.get(f"/documents/{row['id']}").json()["text"]


# ---- open / reveal ----
@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(appmod, "_mac_open", lambda *a: calls.append(a))
    return calls


@pytest.mark.parametrize("name", ["run.command", "Thing.APP", "x.sh", "deploy.py", "link.webloc"])
def test_open_refuses_code(opened: list, name: str) -> None:
    row = upload(name, f"echo {name}".encode(), mime="text/plain")
    r = client.post(f"/documents/{row['id']}/open")
    assert r.status_code == 400 and "can run code" in r.json()["detail"] and not opened


def test_open_refuses_an_exec_bit_and_opens_a_pdf(opened: list) -> None:
    row = upload("tool.pdf", b"%PDF-1 exec")
    Path(row["path"]).chmod(0o755)
    assert client.post(f"/documents/{row['id']}/open").status_code == 400 and not opened
    Path(row["path"]).chmod(0o644)
    assert client.post(f"/documents/{row['id']}/open").json() == {"ok": True}
    assert opened == [(row["path"],)]


def test_reveal_calls_the_opener(opened: list) -> None:
    row = upload("script.sh", b"echo hi", mime="text/plain")
    assert client.post(f"/documents/{row['id']}/reveal").json() == {"ok": True}
    assert opened == [("-R", row["path"])]
