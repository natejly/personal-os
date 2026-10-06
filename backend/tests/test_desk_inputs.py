"""Desk inputs: docs, uploaded documents and local files handed to a desk as read-only snapshots under inputs/.

Run: PERSONAL_OS_DATA_DIR=/tmp/x backend/.venv/bin/python -m pytest backend/tests/test_desk_inputs.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="deskinputs-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod  # noqa: E402
from personal_os import fsx  # noqa: E402
from personal_os.app import AUTH_TOKEN, app, docs, documents, workspace  # noqa: E402
from personal_os.cowork import desk_manual  # noqa: E402
from personal_os.workspace import Workspace, WorkspaceError  # noqa: E402

client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = (tmp_path / "home").resolve()
    h.mkdir()
    monkeypatch.setattr(appmod.mac, "home", lambda: h)
    return h


def new_desk(**kw: object) -> dict:
    r = client.post("/cowork/desks", json={"brief": "Summarise the inputs", "start": False, **kw})
    assert r.status_code == 200, r.text
    return r.json()["desk"]


def test_doc_input_lands_with_a_manifest_line_and_is_in_the_manual() -> None:
    d = docs.create("Q3 Targets", "Acme, Globex")
    desk = new_desk(inputs=[{"kind": "doc", "id": d["id"]}])
    root = workspace.desk_root(desk["id"])
    assert (root / "inputs" / "Q3 Targets.md").read_text() == "Acme, Globex"
    manifest = (root / "inputs" / "MANIFEST.md").read_text()
    assert "`inputs/Q3 Targets.md`" in manifest and d["id"] in manifest
    listed = workspace.inputs(desk["id"])
    assert [e["path"] for e in listed] == ["inputs/Q3 Targets.md"] and listed[0]["state"] == "unchanged"
    text = desk_manual({"desk_read_file"}, {"inputs": listed})
    assert "inputs/Q3 Targets.md" in text and "MANIFEST.md" in text


def test_uploaded_document_copies_its_stored_bytes(tmp_path: Path) -> None:
    stored = tmp_path / "report.pdf"
    stored.write_bytes(b"%PDF-1.4 \x00 bytes")
    doc = documents.create(None, "report.pdf", "application/pdf", stored.stat().st_size, str(stored), "extracted")
    desk = new_desk()
    r = client.post(f"/cowork/desks/{desk['id']}/inputs", json={"inputs": [{"kind": "document", "id": doc["id"]}]})
    assert r.status_code == 200, r.text
    assert [a["path"] for a in r.json()["added"]] == ["inputs/report.pdf"]
    assert (workspace.desk_root(desk["id"]) / "inputs" / "report.pdf").read_bytes() == stored.read_bytes()


def test_local_path_may_be_anywhere_but_the_protected_places(home: Path, tmp_path: Path) -> None:
    inside = home / "notes.txt"
    inside.write_text("hello")
    desk = new_desk(inputs=[{"kind": "path", "path": str(inside)}])
    assert (workspace.desk_root(desk["id"]) / "inputs" / "notes.txt").read_text() == "hello"

    outside = tmp_path / "elsewhere.txt"  # outside the home folder: fine
    outside.write_text("fine")
    other = new_desk(inputs=[{"kind": "path", "path": str(outside)}])
    assert (workspace.desk_root(other["id"]) / "inputs" / "elsewhere.txt").read_text() == "fine"
    link = home / "sneaky.txt"
    link.symlink_to(outside)
    r = client.post(f"/cowork/desks/{desk['id']}/inputs", json={"inputs": [{"kind": "path", "path": str(link)}]})
    assert r.status_code == 200, "a symlink is judged where it points, and that is fine"
    (home / "id_rsa").write_text("secret")
    r = client.post(f"/cowork/desks/{desk['id']}/inputs", json={"inputs": [{"kind": "path", "path": str(home / 'id_rsa')}]})
    assert r.status_code == 400, "credential files are refused"
    grains = workspace.desk_root(desk["id"]) / "inputs" / "notes.txt"  # Grain's own data folder
    r = client.post(f"/cowork/desks/{desk['id']}/inputs", json={"inputs": [{"kind": "path", "path": str(grains)}]})
    assert r.status_code == 400 and "off limits" in r.text


def test_oversize_inputs_are_refused_by_quota(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "data", max_total_bytes=1000)
    with pytest.raises(WorkspaceError, match="limit"):
        ws.add_inputs("d1", [("big.bin", b"x" * 600, "test")])
    assert not (ws.desk_root("d1") / "inputs" / "big.bin").exists(), "a refused batch lands nothing"
    assert ws.add_inputs("d1", [("small.txt", b"ok", "test")])[0]["path"] == "inputs/small.txt"


def test_inputs_are_read_only_to_desk_writers(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "data")
    ws.add_inputs("d1", [("x.md", b"original", "test")])
    for call in (lambda: ws.write("d1", "inputs/x.md", "changed", mode="overwrite"),
                 lambda: ws.write("d1", "Inputs/new.md", "new"),
                 lambda: ws.trash("d1", "inputs/x.md"),
                 lambda: ws.reserve_file("d1", "inputs/download.bin")):
        with pytest.raises(WorkspaceError, match="read-only"):
            call()
    g = fsx.Grants(ws.desk_root("d1").resolve())
    with pytest.raises(fsx.FsError, match="read-only"):
        fsx.resolve_path("inputs/x.md", g, write=True)
    # A shell write gets past the guards; the baseline makes it show up instead of passing silently.
    (ws.desk_root("d1") / "inputs" / "x.md").write_text("tampered")
    assert ws.inputs("d1")[0]["state"] == "modified"
    assert "changed since it was handed in" in desk_manual(set(), {"inputs": ws.inputs("d1")})


def test_unknown_kind_and_missing_doc() -> None:
    assert client.post("/cowork/desks", json={"brief": "x", "start": False, "inputs": [{"kind": "url", "path": "x"}]}).status_code == 400
    assert client.post("/cowork/desks", json={"brief": "x", "start": False, "inputs": [{"kind": "doc", "id": "nope"}]}).status_code == 404


def test_a_lost_upload_falls_back_to_named_extracted_text(tmp_path: Path) -> None:
    doc = documents.create(None, "gone.pdf", "application/pdf", 10, str(tmp_path / "missing.pdf"), "the text")
    desk = new_desk(inputs=[{"kind": "document", "id": doc["id"]}])
    root = workspace.desk_root(desk["id"])
    assert (root / "inputs" / "gone.pdf.txt").read_text() == "the text"
    assert not (root / "inputs" / "gone.pdf").exists()
    assert "extracted text of uploaded document" in (root / "inputs" / "MANIFEST.md").read_text()


def test_local_paths_are_sized_together_before_any_is_read(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for n in ("a.txt", "b.txt"):
        (home / n).write_bytes(b"x" * 300)
    reads: list[Path] = []
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: reads.append(self) or real(self))
    monkeypatch.setattr(workspace, "max_total_bytes", 1000)   # each fits alone (600 <= 1000), both do not (1200)
    r = client.post("/cowork/desks", json={"brief": "x", "start": False,
                                           "inputs": [{"kind": "path", "path": str(home / n)} for n in ("a.txt", "b.txt")]})
    assert r.status_code == 400 and "left" in r.text, r.text
    assert not any(p.parent == home for p in reads), "nothing was read before the batch was refused"


def test_manifest_has_a_baseline_so_tampering_shows(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "data")
    ws.add_inputs("d1", [("a.md", b"a", "test")])
    ws.add_inputs("d1", [("b.md", b"b", "test")])
    state = lambda: {e["path"]: e["state"] for e in ws.tree("d1", "inputs")}["inputs/MANIFEST.md"]  # noqa: E731
    assert state() == "unchanged", "appends by add_inputs refresh the baseline"
    (ws.desk_root("d1") / "inputs" / "MANIFEST.md").write_text("rewritten")
    assert state() == "modified"
