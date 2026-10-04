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


def test_local_path_must_be_under_home(home: Path, tmp_path: Path) -> None:
    inside = home / "notes.txt"
    inside.write_text("hello")
    desk = new_desk(inputs=[{"kind": "path", "path": str(inside)}])
    assert (workspace.desk_root(desk["id"]) / "inputs" / "notes.txt").read_text() == "hello"

    outside = tmp_path / "elsewhere.txt"
    outside.write_text("nope")
    r = client.post("/cowork/desks", json={"brief": "x", "start": False, "inputs": [{"kind": "path", "path": str(outside)}]})
    assert r.status_code == 400 and "home" in r.text
    link = home / "sneaky.txt"
    link.symlink_to(outside)
    r = client.post(f"/cowork/desks/{desk['id']}/inputs", json={"inputs": [{"kind": "path", "path": str(link)}]})
    assert r.status_code == 400, "a symlink that escapes home is refused"
    (home / "id_rsa").write_text("secret")
    r = client.post(f"/cowork/desks/{desk['id']}/inputs", json={"inputs": [{"kind": "path", "path": str(home / 'id_rsa')}]})
    assert r.status_code == 400, "credential files are refused"


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
    g = fsx.Grants([], ws.desk_root("d1").resolve())
    with pytest.raises(fsx.FsError, match="read-only"):
        fsx.resolve_path("inputs/x.md", g, write=True)
    # A shell write gets past the guards; the baseline makes it show up instead of passing silently.
    (ws.desk_root("d1") / "inputs" / "x.md").write_text("tampered")
    assert ws.inputs("d1")[0]["state"] == "modified"
    assert "changed since it was handed in" in desk_manual(set(), {"inputs": ws.inputs("d1")})


def test_unknown_kind_and_missing_doc() -> None:
    assert client.post("/cowork/desks", json={"brief": "x", "start": False, "inputs": [{"kind": "url", "path": "x"}]}).status_code == 400
    assert client.post("/cowork/desks", json={"brief": "x", "start": False, "inputs": [{"kind": "doc", "id": "nope"}]}).status_code == 404
