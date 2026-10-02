# Producing a .xlsx with openpyxl or xlsxwriter

- `openpyxl`: read and modify existing workbooks, simple writes.
- `xlsxwriter`: write-only, best for new, polished workbooks (formats, native charts, conditional formatting). It cannot open an existing file.
- `pandas`: `df.to_excel(path, engine="xlsxwriter")` for dumping tables, then format via the writer.

## Skeleton (write under outputs/)
```python
import xlsxwriter
wb = xlsxwriter.Workbook("outputs/budget.xlsx")
ws = wb.add_worksheet("Budget")
hdr = wb.add_format({"bold": True, "bg_color": "#DDEBF7", "border": 1})
money = wb.add_format({"num_format": "$#,##0.00"})
pct = wb.add_format({"num_format": "0.0%"})

rows = [("Rent", 1800.0), ("Food", 600.0), ("Fun", 250.0)]
total = sum(a for _, a in rows)
ws.write_row(0, 0, ["Category", "Amount", "Share"], hdr)
for i, (name, amt) in enumerate(rows, start=1):
    ws.write(i, 0, name)
    ws.write_number(i, 1, amt, money)
    ws.write_formula(i, 2, f"=B{i+1}/B${len(rows)+2}", pct, amt / total)   # last arg = cached value
ws.write(len(rows) + 1, 0, "Total", hdr)
ws.write_formula(len(rows) + 1, 1, f"=SUM(B2:B{len(rows)+1})", money, total)

ws.set_column("A:A", 24); ws.set_column("B:C", 14)
ws.freeze_panes(1, 0)
wb.close()          # nothing is written until close()
```

## What bites
- Formulas are stored as text. No cached result exists until a spreadsheet app recalculates, so a reader that does not calculate (pandas, a previewer, openpyxl with `data_only=True`) sees empty or zero. When the number matters, compute it in Python too: xlsxwriter takes the cached value as the last argument of `write_formula`; with openpyxl you cannot, so state the figure in your summary as well.
- Never hard-code a number that is derived from other cells. A model is inputs, formulas, outputs. Put assumptions in labelled input cells and reference them.
- Number formats are display only (`$#,##0.00`, `0.0%`, `yyyy-mm-dd`, `#,##0`). Store real numbers and real dates (`datetime`), never strings like "12%" or "1,200".
- Column widths are not automatic. Set them; wide text needs `text_wrap`.
- Freeze the header row; add `ws.autofilter(0, 0, last_row, last_col)` for tables.
- Sheet names: at most 31 characters, none of `[]:*?/\`. A sheet name with spaces needs quotes in formulas: `='My Sheet'!A1`.
- openpyxl is 1-based (`ws.cell(row=1, column=1)`); xlsxwriter is 0-based (`write(0, 0, ...)`). Mixing them silently shifts everything by one.
- Formulas use English function names and commas, whatever the user's locale.
- Missing library: call `python_install` with `xlsxwriter` or `openpyxl`.

## Check your work (mandatory)
1. Reopen with `openpyxl.load_workbook(path)` (formulas visible) and assert: sheet names, header row, row count, that formula cells start with "=", no cell holds an error literal such as `#REF!`.
2. Compare each total against a Python recomputation from the source data.
3. `render_preview` the file and `view_image` the first page: check column widths (no `####`), wrapped text, number formats, header styling. Fix and repeat at most twice.
If LibreOffice is missing, `render_preview` cannot check layout here; say that in your summary.
