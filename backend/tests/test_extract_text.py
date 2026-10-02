"""Uploads of every kind should be accepted. Run: python backend/tests/test_extract_text.py"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os.extract_text import (MAX_INDEX_CHARS, _bounded, extract_text, for_index,  # noqa: E402
                                      safe_upload_name)


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


if __name__ == "__main__":
    test_text_code_and_unknown_extensions()
    test_binary_is_stored_as_a_note_instead_of_rejected()
    test_upload_names_cannot_leave_the_uploads_folder()
    test_index_and_pdf_text_are_capped()
    print("ok")
