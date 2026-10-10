"""The `show` tool and the /local/raw route behind the chat's side panel: the content rides on the tool event
(never to the model), and a file on this Mac is served under the same home-folder guard as read_local_file.

Run: uv run --project backend --with pytest pytest backend/tests/test_show.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="showtest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as appmod, tools  # noqa: E402
from personal_os.tools import Toolbox  # noqa: E402

client = TestClient(appmod.app, headers={"X-Personal-OS-Token": appmod.AUTH_TOKEN})
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = (tmp_path / "home").resolve()
    (h / "Documents").mkdir(parents=True)
    (h / "Library").mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def _show(**args: object) -> dict:
    tb = Toolbox(None, None, None, lambda: {})  # type: ignore[arg-type]
    return asyncio.run(tb.specs["show"].fn({}, **args))


def test_show_is_a_core_tool_the_model_always_has() -> None:
    tb = Toolbox(None, None, None, lambda: {})  # type: ignore[arg-type]
    spec = tb.specs["show"]
    assert spec.group == "utility" and spec.danger == "safe" and not spec.taints
    assert tools.is_core(spec) if hasattr(tools, "is_core") else spec.group in tools.CORE_GROUPS


def test_inline_content_rides_under_show_with_a_receipt_for_the_model() -> None:
    out = _show(kind="mermaid", content="graph TD\n  A --> B", title="Flow")
    assert out["show"] == {"kind": "mermaid", "title": "Flow", "source": "graph TD\n  A --> B"}
    assert out["shown"] == "Flow" and "side panel" in out["note"]
    # The title falls back to the kind; empty content and unknown kinds are errors the model can act on.
    assert _show(kind="html", content="<p>x</p>")["show"]["title"] == "html"
    assert _show(kind="html", content="  ")["error"].startswith("show: content is required")
    assert "kind must be one of" in _show(kind="pdf", content="x")["error"]
    assert "limit is" in _show(kind="markdown", content="x" * (tools.SHOW_MAX_CHARS + 1))["error"]


def test_a_file_is_named_and_sized_but_its_bytes_never_reach_the_model(home: Path) -> None:
    p = home / "Documents" / "Lease 2026.pdf"
    p.write_bytes(PDF)
    out = _show(kind="file", path="~/Documents/Lease 2026.pdf")
    assert out["show"] == {"kind": "file", "title": "Lease 2026.pdf", "path": str(p), "name": "Lease 2026.pdf",
                           "mime": "application/pdf", "size": len(PDF)}
    assert out["shown"] == "Lease 2026.pdf"
    assert PDF.decode() not in str(out)


def test_anywhere_on_the_mac_but_a_credential_store_or_grains_own_folder(home: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere.pdf"
    outside.write_bytes(PDF)
    assert "error" not in _show(kind="file", path=str(outside)), "outside the home folder is fine now"
    (home / "Library" / "notes.pdf").write_bytes(PDF)
    assert "error" not in _show(kind="file", path="~/Library/notes.pdf")
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_ed25519").write_text("key")
    assert "secrets" in _show(kind="file", path="~/.ssh/id_ed25519")["error"]
    assert "off limits" in _show(kind="file", path="/Applications/Grain.app/Contents/Info.plist")["error"]
    assert "is not a file" in _show(kind="file", path="~/Documents")["error"]
    assert "is not a file" in _show(kind="file", path="~/Documents/missing.pdf")["error"]


def test_local_raw_serves_a_home_file_with_its_type_and_html_as_text(home: Path, tmp_path: Path) -> None:
    (home / "Documents" / "a.pdf").write_bytes(PDF)
    r = client.get("/local/raw", params={"path": str(home / "Documents" / "a.pdf")})
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.headers["x-content-type-options"] == "nosniff"
    # An HTML (or SVG) file is text here: the panel renders it through the sandboxed frame, never as a page.
    (home / "Documents" / "page.html").write_text("<script>alert(1)</script>")
    r = client.get("/local/raw", params={"path": "~/Documents/page.html"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    (home / "Documents" / "logo.svg").write_text("<svg/>")
    assert client.get("/local/raw", params={"path": "~/Documents/logo.svg"}).headers["content-type"].startswith("text/plain")
    # Anywhere on the Mac is served; a credential store is refused, and a folder or missing file is not served either.
    outside = tmp_path / "x.pdf"
    outside.write_bytes(PDF)
    assert client.get("/local/raw", params={"path": str(outside)}).status_code == 200
    (home / ".aws").mkdir()
    (home / ".aws" / "credentials").write_text("k")
    assert client.get("/local/raw", params={"path": "~/.aws/credentials"}).status_code == 400
    assert client.get("/local/raw", params={"path": "~/Documents"}).status_code == 404
    assert client.get("/local/raw", params={"path": "~/Documents/none.pdf"}).status_code == 404


def test_local_stat_says_whether_a_named_path_opens(home: Path) -> None:
    (home / "Documents" / "a.pdf").write_bytes(PDF)
    r = client.get("/local/stat", params={"path": "~/Documents/a.pdf"})
    assert r.status_code == 200 and r.json()["name"] == "a.pdf" and r.json()["size"] == len(PDF)
    assert client.get("/local/stat", params={"path": "~/Documents/none.pdf"}).status_code == 404
    (home / ".aws").mkdir()
    (home / ".aws" / "credentials").write_text("k")
    assert client.get("/local/stat", params={"path": "~/.aws/credentials"}).status_code == 400


def test_local_raw_needs_the_app_token() -> None:
    assert TestClient(appmod.app).get("/local/raw", params={"path": "~/Documents/a.pdf"}).status_code == 401


def test_pane_rides_on_the_payload_and_a_bad_value_is_a_tool_error() -> None:
    assert _show(kind="markdown", content="# a", pane="right")["show"]["pane"] == "right"
    assert "pane" not in _show(kind="markdown", content="# a")["show"]
    bad = _show(kind="markdown", content="# a", pane="middle")
    assert bad["error"].startswith("show: pane must be")
