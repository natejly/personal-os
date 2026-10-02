# Producing and handling PDFs

- `reportlab` creates PDFs. Use **platypus** (flowables) for documents that flow across pages; use the low-level **canvas** only for fixed layouts such as a certificate or label.
- `pypdf` merges, splits, rotates, stamps and fills forms.
- `pdfplumber` extracts text and tables from a PDF (`convert_document` to txt is the quick alternative).
- For a document that starts as prose, writing a .docx and using `convert_document` (to pdf) is often simpler.

## Skeleton: platypus (write under outputs/)
```python
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image

ss = getSampleStyleSheet()
doc = SimpleDocTemplate("outputs/report.pdf", pagesize=A4, leftMargin=2*cm, rightMargin=2*cm,
                        topMargin=2*cm, bottomMargin=2*cm, title="Report")
story = [Paragraph("Report", ss["Title"]), Spacer(1, 12),
         Paragraph("Body text with <b>inline</b> markup.", ss["BodyText"])]
tbl = Table([["Item", "Cost"], ["Widget", "$12.00"]], colWidths=[8*cm, 4*cm], repeatRows=1)
tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                         ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                         ("ALIGN", (1, 0), (1, -1), "RIGHT")]))
story += [Spacer(1, 12), tbl]
if os.path.exists("work/chart.png"):                   # made as in the charts guide
    story.append(Image("work/chart.png", width=14*cm, height=7*cm))

def footer(c, d):                      # page template callback: page numbers
    c.setFont("Helvetica", 8); c.drawRightString(A4[0] - 2*cm, 1*cm, f"Page {d.page}")
doc.build(story, onFirstPage=footer, onLaterPages=footer)
```
Merge: `from pypdf import PdfReader, PdfWriter`; `w = PdfWriter()`; for each file `for p in PdfReader(f).pages: w.add_page(p)`; then `w.write("outputs/merged.pdf")`.
Extract: `with pdfplumber.open(path) as pdf: text = pdf.pages[0].extract_text(); tables = pdf.pages[0].extract_tables()`.

## What bites
- Paragraph text is a small XML dialect: escape `&` as `&amp;` and `<` as `&lt;`, close every tag, use `<br/>` for line breaks. A plain "\n" is ignored.
- Built-in fonts (Helvetica, Times, Courier) lack glyphs for emoji and most non-Latin scripts. Register a TTF with `pdfmetrics.registerFont(TTFont("Name", "path.ttf"))` and use it in the styles.
- Long tables: `repeatRows=1` repeats the header; put `Paragraph` objects in cells to get wrapping, plain strings never wrap.
- Images: give width and height in the picture's own ratio (read it from `PIL.Image.open(p).size`).
- `PageBreak()` forces a new page; `KeepTogether([...])` stops a heading being stranded at a page bottom.
- Scanned PDFs have no text layer: extraction returns nothing. Say so rather than inventing content.
- Missing library: call `python_install` with `reportlab`, `pypdf` or `pdfplumber`.

## Check your work (mandatory)
1. Reopen with `pypdf.PdfReader(path)`: assert the page count and that `extract_text()` on key pages contains the expected headings and figures.
2. `render_preview` the file and `view_image` the first page and any page with a table or image: look for clipped text, tables running off the page, an overlapping footer, blank pages.
3. Fix and repeat at most twice.
