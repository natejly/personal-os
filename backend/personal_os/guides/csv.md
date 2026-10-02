# Producing CSV (and TSV) files

Use the standard `csv` module for small, hand-built files and `pandas` for tables you already hold as a DataFrame. A CSV carries no types, formats or formulas: if the user needs those, write a .xlsx instead (see the xlsx guide).

## Skeleton (write under outputs/)
```python
import csv

rows = [("name", "joined", "score"), ("Ana, Jr.", "2026-03-04", 91.5), ("Bo \"B\"", "2026-04-11", 78.0)]
with open("outputs/people.csv", "w", newline="", encoding="utf-8-sig") as f:   # newline="" is required
    csv.writer(f).writerows(rows)

# pandas equivalent
import pandas as pd
df = pd.DataFrame(rows[1:], columns=rows[0])
df.to_csv("outputs/people.csv", index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
```

## What bites
- Quoting: fields containing commas, quotes or newlines must be quoted and inner quotes doubled. Never build lines with `",".join(...)`; the csv module does this correctly.
- Open the file with `newline=""` for the csv module, or doubled line endings appear.
- Encoding: `utf-8-sig` (UTF-8 with a byte-order mark) makes spreadsheet apps show accents and non-Latin text correctly. Use plain `utf-8` when the file feeds a program or a database import.
- Dates as ISO 8601 (`2026-03-04`, or `2026-03-04T09:30:00`), never `3/4/26`, whose month/day order is ambiguous.
- Spreadsheet apps drop leading zeros and turn long digit strings into scientific notation (`007` becomes `7`). If an id must survive, say so in the summary, or deliver an .xlsx with the column stored as text.
- Text beginning with `=`, `+`, `-` or `@` can be run as a formula when opened in a spreadsheet: prefix untrusted values with an apostrophe or a space.
- Use `.` for decimals and no thousands separators. Keep one header row, unique column names, one record per row, no blank spacer rows, no totals rows mixed into data.
- Tab-separated: `csv.writer(f, delimiter="\t")` and a `.tsv` extension.
- Reading a big file: `pd.read_csv(path, nrows=5)` first to look, then the whole thing; pass `dtype=str` to keep ids intact.
- Missing library: call `python_install` with `pandas`.

## Check your work (mandatory)
1. Reopen with `csv.reader` (or `pd.read_csv(path, dtype=str)`): assert the header, the row count, and that every row has the same number of fields; spot-check a row that contains a comma or a quote.
2. Print the first lines with `desk_read_file` and read them as a person would: dates ISO, no stray quotes, no `nan` text where a cell should be empty (`na_rep=""`).
3. Fix and repeat at most twice. CSV has no layout, so there is nothing to render.
