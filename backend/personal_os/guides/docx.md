# Producing a .docx with python-docx

Use `python-docx` for reports, letters, memos. For prose you already have as markdown, `convert_document` (md -> docx) is faster; use python-docx when you need tables, images, headers or exact styling.

## Skeleton (run with run_python; write under outputs/)
```python
from docx import Document
from docx.shared import Inches, Pt

doc = Document()
st = doc.styles["Normal"]; st.font.name = "Calibri"; st.font.size = Pt(11)
for s in doc.sections:
    s.left_margin = s.right_margin = Inches(1)

doc.add_heading("Quarterly report", level=0)      # level 0 = title
doc.add_heading("1. Summary", level=1)
doc.add_paragraph("Body text goes here.")
doc.add_paragraph("A bullet", style="List Bullet")
doc.add_paragraph("A numbered step", style="List Number")

t = doc.add_table(rows=1, cols=3); t.style = "Light Grid Accent 1"
for c, h in zip(t.rows[0].cells, ["Item", "Qty", "Cost"]):
    c.text = h
    c.paragraphs[0].runs[0].bold = True
row = t.add_row().cells; row[0].text, row[1].text, row[2].text = "Widget", "4", "$12.00"

doc.add_picture("work/chart.png", width=Inches(6))  # width only: height keeps the aspect ratio
doc.add_page_break()
doc.save("outputs/report.docx")
```

## What bites
- Use styles ("Heading 1", "List Bullet", "Title") instead of bolding and resizing by hand. Headings must nest: 1, then 2, never 1 straight to 3. Styles are what give the file a navigable outline.
- Set formatting on a run (`p.add_run("x").bold = True`), not on the paragraph object, which has no font.
- `doc.add_paragraph(text)` does not parse markdown. No `**bold**` or `# heading`: build runs and headings explicitly.
- Table cells hold paragraphs; `cell.text = ...` replaces them. Set column widths on every cell of the column, not just the first row.
- Images: always give `width=Inches(...)` and stay within the 6.5 in text width on a letter page with 1 in margins.
- Page breaks are explicit (`add_page_break()`); do not pad with blank paragraphs.
- A tab or run of spaces does not align columns, use a table.
- Headers and footers live on `doc.sections[0].header` / `.footer`. Page-number fields need raw XML; skip them unless asked.
- A missing library (`ModuleNotFoundError: docx`) means the work environment lacks it: call `python_install` with `python-docx`.

## Check your work (mandatory)
1. Reopen with `Document("outputs/report.docx")` and assert the essentials: heading texts and order, number of tables, row counts, that no placeholder text such as "TODO" or "lorem" remains.
2. Call `render_preview` on the file, then `view_image` on the pages that matter (first page, any page with a table or image). Look for: overflowing tables, orphaned headings at the bottom of a page, images too large or clipped, wrong fonts.
3. Fix and repeat. Stop after two rounds of fixes and tell the user what is still imperfect.
If `render_preview` reports that LibreOffice is missing, the layout cannot be checked visually here: say so in your summary instead of claiming it looks right.
