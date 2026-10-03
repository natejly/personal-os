"""Uploads of every kind should be accepted. Run: python backend/tests/test_extract_text.py"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="extracttest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os.extract_text import (MAX_INDEX_CHARS, _bounded, extract_text, for_index,  # noqa: E402
                                      has_readable_text, safe_upload_name)


def test_text_code_and_unknown_extensions() -> None:
    assert extract_text("notes.md", b"# Hi\n") == "# Hi\n"
    assert "fn main" in extract_text("main.rs", b"fn main() {}\n")
    assert extract_text("noext", b"plain words\n") == "plain words\n"


def test_binary_is_stored_as_a_note_instead_of_rejected() -> None:
    note = extract_text("photo.png", b"\x89PNG\r\n\x00\xff", "image/png")
    assert "photo.png" in note and "No text could be extracted" in note
    assert "scan.xlsx" in extract_text("scan.xlsx", b"PK\x03\x04\x00\x00")


def test_upload_names_cannot_leave_the_uploads_folder() -> None:
    assert safe_upload_name("../../etc/passwd") == "passwd"
    assert safe_upload_name("..\\..\\secrets.txt") == "secrets.txt"
    assert safe_upload_name("a\x00b.txt") == "ab.txt"
    assert safe_upload_name("report\u202e.txt") == "report.txt"
    assert safe_upload_name("..\u202e") == "untitled"
    assert safe_upload_name("..") == "untitled"
    assert safe_upload_name("") == "untitled"
    assert safe_upload_name("notes.md") == "notes.md"


def test_index_and_pdf_text_are_capped() -> None:
    big = "a" * (MAX_INDEX_CHARS + 50)
    indexed = for_index(big)
    assert len(indexed) < len(big)
    assert indexed.endswith("The full file is stored.]")
    capped = _bounded(["page one", "page two"], 1)
    assert capped.startswith("page one")
    assert "page two" not in capped
    assert "truncated" in capped


def test_has_readable_text_tells_a_marker_from_a_document() -> None:
    assert not has_readable_text(extract_text("photo.png", b"\x89PNG\r\n\x00\xff", "image/png"))
    assert not has_readable_text(extract_text("blob.bin", b"\x00\x01\x02\xff", "application/octet-stream"))
    assert not has_readable_text("")
    assert not has_readable_text("  \n\t ")
    assert has_readable_text("# Hi\n")
    # The OCR note that precedes a scanned page's text is a marker with text after it, so it reads.
    assert has_readable_text("[OCR text: this PDF has no text layer, so the words below were read from page images and may contain mistakes]\n\nhello")
    marker = extract_text("photo.png", b"\x89PNG\r\n\x00\xff", "image/png")
    assert has_readable_text(f"The upload said:\n\n{marker}\n\nwhich is what we expected.")
    assert has_readable_text(marker + " and then a sentence")


def test_upload_route_reports_whether_the_file_can_be_read() -> None:
    """A picture is stored, listed and findable by name, but the client is told the assistant cannot read it."""
    from fastapi.testclient import TestClient

    from personal_os import app as appmod
    from personal_os.app import AUTH_TOKEN, app

    client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\xff"
    png = client.post("/documents", files={"file": ("shot.png", png_bytes, "image/png")})
    assert png.status_code == 200, png.text
    row = png.json()
    assert row["extracted"] is False and row["readable"] is False
    assert "cannot see" in row["reason"]
    assert any(d["id"] == row["id"] for d in client.get("/documents").json())
    again = client.post("/documents", files={"file": ("shot.png", png_bytes, "image/png")}).json()
    assert again["duplicate"] is True and again["extracted"] is False and again["readable"] is False
    md = client.post("/documents", files={"file": ("notes.md", b"# Plan\n\nShip it.\n", "text/markdown")}).json()
    assert md["extracted"] is True and md["readable"] is True and "reason" not in md
    md2 = client.post("/documents", files={"file": ("notes.md", b"# Plan\n\nShip it.\n", "text/markdown")}).json()
    assert md2["duplicate"] is True and md2["extracted"] is True and md2["readable"] is True
    # _store_upload itself carries the flag on both exits.
    direct = appmod._store_upload(None, "words.txt", "text/plain", b"plain words\n")
    assert direct["extracted"] is True
    assert appmod._store_upload(None, "words.txt", "text/plain", b"plain words\n")["duplicate"] is True


if __name__ == "__main__":
    test_text_code_and_unknown_extensions()
    test_binary_is_stored_as_a_note_instead_of_rejected()
    test_upload_names_cannot_leave_the_uploads_folder()
    test_index_and_pdf_text_are_capped()
    test_has_readable_text_tells_a_marker_from_a_document()
    test_upload_route_reports_whether_the_file_can_be_read()
    print("ok")
