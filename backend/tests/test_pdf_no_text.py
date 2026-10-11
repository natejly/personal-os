"""A PDF with no text layer and no OCR gets a note that names the fallback routes, and still counts as unreadable."""
import io

from personal_os import extract_text as et


def _blank_pdf() -> bytes:
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(200, 200)
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


def test_textless_pdf_gets_fallback_note(monkeypatch):
    monkeypatch.setattr(et, "_which", lambda b: None)
    text = et.extract_text("scan.pdf", _blank_pdf(), "application/pdf")
    assert "render_preview" in text and "web_search" in text and "not installed" in text
    assert not et.has_readable_text(text)
