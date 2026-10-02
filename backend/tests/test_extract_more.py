"""extract_text reads spreadsheets, slides, images and scanned PDFs. The office files are written by hand (zip + XML) so
the test needs neither openpyxl nor python-pptx, and every external binary is faked: no real tesseract or pdftoppm runs."""
from __future__ import annotations

import io
import os
import sys
import zipfile
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from personal_os import extract_text as ex  # noqa: E402

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _zip(parts: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in parts.items():
            z.writestr(k, v)
    return buf.getvalue()


def _xlsx() -> bytes:
    return _zip({
        "xl/workbook.xml": f'<workbook xmlns="{NS_MAIN}" xmlns:r="{NS_REL}"><sheets>'
                           '<sheet name="Sales" sheetId="1" r:id="rId1"/><sheet name="Empty" sheetId="2" r:id="rId2"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                      '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>',
        "xl/sharedStrings.xml": f'<sst xmlns="{NS_MAIN}"><si><t>Region</t></si><si><t>Total</t></si><si><r><t>No</t></r><r><t>rth</t></r></si></sst>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{NS_MAIN}"><dimension ref="A1:C3"/><sheetData>'
                                    '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                                    '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>12.0</v></c><c r="C2"><f>B2*2</f><v>24</v></c></row>'
                                    '<row r="3"><c r="A3" t="inlineStr"><is><t>South</t></is></c><c r="C3" t="b"><v>1</v></c></row>'
                                    '</sheetData></worksheet>',
        "xl/worksheets/sheet2.xml": f'<worksheet xmlns="{NS_MAIN}"><sheetData/></worksheet>',
    })


def test_xlsx_stdlib_reader() -> None:
    out = ex._xlsx_stdlib(_xlsx())
    assert out is not None
    assert "## Sheet: Sales (A1:C3)" in out
    assert "| Region | Total |" in out
    assert "| North | 12 | 24 |" in out  # shared string with runs, 12.0 shown as 12, the formula's cached value
    assert "| South |  | TRUE |" in out
    assert "## Sheet: Empty" in out and "(empty)" in out
    # the public entry point reaches it (whichever of the two readers is installed)
    assert "North" in ex.extract_text("book.xlsx", _xlsx())


def test_xlsx_row_cap_says_what_was_cut() -> None:
    rows = "".join(f'<row r="{i}"><c r="A{i}"><v>{i}</v></c></row>' for i in range(1, 501))
    data = _zip({
        "xl/workbook.xml": f'<workbook xmlns="{NS_MAIN}"><sheets><sheet name="Big" sheetId="1"/></sheets></workbook>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{NS_MAIN}"><sheetData>{rows}</sheetData></worksheet>',
    })
    out = ex._xlsx_stdlib(data)
    assert out and f"showing the first {ex.SHEET_ROWS} of 500 non-empty rows" in out
    assert "| 200 |" in out and "| 201 |" not in out


def _slide(title: str, bullets: list[str], table: bool = False) -> str:
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    body = "".join(f"<a:p><a:pPr lvl=\"{1 if b.startswith('>') else 0}\"/><a:r><a:t>{b.lstrip('>')}</a:t></a:r></a:p>" for b in bullets)
    tbl = ('<p:graphicFrame><a:graphic><a:graphicData><a:tbl><a:tr><a:tc><a:txBody><a:p><a:r><a:t>H1</a:t></a:r></a:p></a:txBody></a:tc>'
           '<a:tc><a:txBody><a:p><a:r><a:t>H2</a:t></a:r></a:p></a:txBody></a:tc></a:tr><a:tr><a:tc><a:txBody><a:p><a:r><a:t>v1</a:t></a:r></a:p></a:txBody></a:tc>'
           '<a:tc><a:txBody><a:p><a:r><a:t>v2</a:t></a:r></a:p></a:txBody></a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>') if table else ""
    return (f'<p:sld xmlns:a="{a}" xmlns:p="{p}"><p:cSld><p:spTree>'
            f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p></p:txBody></p:sp>'
            f'<p:sp><p:nvSpPr><p:nvPr><p:ph idx="1"/></p:nvPr></p:nvSpPr><p:txBody>{body}</p:txBody></p:sp>{tbl}'
            '</p:spTree></p:cSld></p:sld>')


def test_pptx_stdlib_reader_in_order_with_notes_and_tables() -> None:
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    notes = (f'<p:notes xmlns:a="{a}" xmlns:p="{p}"><p:cSld><p:spTree><p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>'
             '<p:txBody><a:p><a:r><a:t>Say this aloud</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>')
    data = _zip({
        "ppt/slides/slide2.xml": _slide("Second", ["table below"], table=True),
        "ppt/slides/slide10.xml": _slide("Tenth", ["last"]),
        "ppt/slides/slide1.xml": _slide("First", ["alpha", ">nested"]),
        "ppt/slides/_rels/slide1.xml.rels": '<Relationships><Relationship Target="../notesSlides/notesSlide1.xml" Type="x/notesSlide"/></Relationships>',
        "ppt/notesSlides/notesSlide1.xml": notes,
    })
    out = ex.extract_text("deck.pptx", data)
    assert out.index("## Slide 1: First") < out.index("## Slide 2: Second") < out.index("## Slide 3: Tenth")  # numeric, not lexical, order
    assert "- alpha" in out and "  - nested" in out
    assert "| H1 | H2 |" in out and "| v1 | v2 |" in out
    assert "Notes: Say this aloud" in out
    blocks = ex.extract_structured("deck.pptx", data)
    assert any(b["kind"] == "heading" and "First" in b["text"] for b in blocks)


def test_damaged_office_files_degrade_to_the_marker() -> None:
    for name in ("a.xlsx", "a.pptx", "a.odt", "a.epub"):
        assert "No text could be extracted" in ex.extract_text(name, b"PK\x03\x04\x00\x00 not really a zip")


def test_csv_is_capped_and_says_so() -> None:
    text = "a,b\n" + "\n".join(f"{i},{i * 2}" for i in range(5000))
    out = ex.extract_text("big.csv", text.encode())
    assert out.startswith("a,b") and f"first {ex.DELIMITED_LINES} of 5001 lines" in out
    assert ex.extract_text("small.csv", b"a,b\n1,2\n") == "a,b\n1,2\n"


def test_rtf_and_epub_and_odt() -> None:
    rtf = rb"{\rtf1\ansi{\fonttbl{\f0 Arial;}}\f0 Hello \b world\b0 !\par Second line}"
    out = ex.extract_text("n.rtf", rtf)
    assert "Hello world!" in out and "Second line" in out and "fonttbl" not in out
    epub = _zip({"OEBPS/ch1.xhtml": "<html><body><h1>One</h1><p>Words &amp; more</p></body></html>"})
    assert "Words & more" in ex.extract_text("b.epub", epub)
    odt = _zip({"content.xml": '<o xmlns:text="urn:t"><text:h>Title</text:h><text:p>Body text</text:p></o>'})
    out = ex.extract_text("d.odt", odt)
    assert "## Title" in out and "Body text" in out


def test_image_marker_without_tesseract(monkeypatch: Any) -> None:
    monkeypatch.setattr(ex, "_which", lambda b: None)
    out = ex.extract_text("shot.png", b"\x89PNG\r\n\x1a\n....", "image/png")
    assert "shot.png" in out and "view_image" in out and "image" in out


def test_image_ocr_with_a_fake_tesseract(monkeypatch: Any) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(ex, "_which", lambda b: "/fake/" + b)

    def run(cmd: list[str], timeout: float, stdin: bytes | None = None) -> bytes:
        calls.append(cmd)
        assert timeout <= ex.OCR_IMAGE_TIMEOUT_S
        return b"Invoice 42\n"

    monkeypatch.setattr(ex, "_run", run)
    out = ex.extract_text("scan.jpg", b"\xff\xd8\xff not a jpeg")
    assert "Invoice 42" in out and calls and calls[0][0] == "tesseract"


def test_scanned_pdf_is_ocred_with_faked_binaries(monkeypatch: Any, tmp_path: Path) -> None:
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(width=200, height=200)  # no text layer at all
    buf = io.BytesIO()
    w.write(buf)
    pdf = buf.getvalue()

    seen: list[str] = []
    monkeypatch.setattr(ex, "_which", lambda b: "/fake/" + b)

    def run(cmd: list[str], timeout: float, stdin: bytes | None = None) -> bytes:
        seen.append(cmd[0])
        assert timeout <= ex.OCR_PAGE_TIMEOUT_S
        if cmd[0] == "pdftoppm":
            page = cmd[cmd.index("-f") + 1]
            Path(cmd[-1] + ".png").write_bytes(b"png" + page.encode())
            return b""
        return b"page text " + (stdin or b"")[3:]

    monkeypatch.setattr(ex, "_run", run)
    out = ex.extract_text("scan.pdf", pdf)
    assert out.startswith("[OCR text") and "page text 1" in out and "page text 3" in out
    assert seen.count("pdftoppm") == 3 and seen.count("tesseract") == 3
    blocks = ex.extract_structured("scan.pdf", pdf)
    assert [b["page"] for b in blocks if b["kind"] == "page"] == [1, 2, 3]

    # without the binaries the old behaviour stands: no text, the plain marker
    monkeypatch.setattr(ex, "_which", lambda b: None)
    assert "No text could be extracted" in ex.extract_text("scan.pdf", pdf) or ex.extract_text("scan.pdf", pdf).strip() == ""


def test_scanned_pdf_stops_at_the_page_limit(monkeypatch: Any) -> None:
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(20):
        w.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    w.write(buf)
    monkeypatch.setattr(ex, "_which", lambda b: "/fake/" + b)

    def run(cmd: list[str], timeout: float, stdin: bytes | None = None) -> bytes:
        if cmd[0] == "pdftoppm":
            Path(cmd[-1] + ".png").write_bytes(b"x")
            return b""
        return b"t"

    monkeypatch.setattr(ex, "_run", run)
    texts, ocr = ex._pdf_page_texts(buf.getvalue())
    assert ocr and len(texts) == ex.OCR_PDF_PAGES
