"""Extract plain text from uploaded files."""
from __future__ import annotations

import io
import re
from pathlib import Path

TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".py", ".ts", ".tsx", ".js", ".html", ".css", ".log", ".rst", ".toml"}


Block = dict  # {kind: 'heading'|'para'|'table'|'page', level: int, text: str, page: int | None}


def _blk(kind: str, text: str = "", level: int = 0, page: int | None = None) -> Block:
    return {"kind": kind, "level": level, "text": text, "page": page}


def _md_table(rows: list[list[str]]) -> str:
    rows = [[(c or "").replace("\n", " ").replace("|", "/").strip() for c in r] for r in rows if r]
    if not rows:
        return ""
    w = max(len(r) for r in rows)
    rows = [r + [""] * (w - len(r)) for r in rows]
    lines = ["| " + " | ".join(r) + " |" for r in rows]
    lines.insert(1, "| " + " | ".join("---" for _ in range(w)) + " |")
    return "\n".join(lines)


def markdown_blocks(text: str, page: int | None = None) -> list[Block]:
    """ATX headings, fenced code kept whole, pipe tables, blank-line paragraphs."""
    out: list[Block] = []
    para: list[str] = []
    fence: list[str] | None = None

    def flush() -> None:
        if para:
            body = "\n".join(para).strip()
            if body:
                is_table = all(ln.lstrip().startswith("|") for ln in para) and len(para) >= 2
                out.append(_blk("table" if is_table else "para", body, page=page))
            para.clear()

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if fence is not None:
            fence.append(line)
            if line.strip().startswith("```"):
                out.append(_blk("para", "\n".join(fence), page=page))
                fence = None
            continue
        if line.strip().startswith("```"):
            flush()
            fence = [line]
            continue
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if m:
            flush()
            out.append(_blk("heading", m.group(2).strip(), level=len(m.group(1)), page=page))
        elif not line.strip():
            flush()
        else:
            para.append(line)
    if fence is not None:  # unterminated fence: keep what we have
        out.append(_blk("para", "\n".join(fence), page=page))
    flush()
    return out


def _guess_heading(para: str) -> int:
    """Conservative PDF heading guess: one short line, no sentence punctuation. 0 = not a heading."""
    line = para.strip()
    if "\n" in line or not 3 <= len(line) <= 80 or line[-1] in ".,;:!?" or not re.search(r"[A-Za-z]", line):
        return 0
    words = line.split()
    if line.isupper():
        return 1
    if 2 <= len(words) <= 6 and all(w[0].isupper() for w in words if len(w) > 3) and sum(1 for w in words if w[0].isupper()) >= 2:
        return 2
    return 0


def extract_structured(name: str, data: bytes, mime: str = "") -> list[Block]:
    """Blocks in reading order: headings, paragraphs, tables and (PDF) page markers."""
    ext = Path(name).suffix.lower()
    if ext == ".pdf" or mime == "application/pdf":
        from pypdf import PdfReader

        out: list[Block] = []
        for n, p in enumerate(PdfReader(io.BytesIO(data)).pages, start=1):
            out.append(_blk("page", page=n))
            for para in re.split(r"\n\s*\n", p.extract_text() or ""):
                para = para.strip()
                if not para:
                    continue
                lvl = _guess_heading(para)
                out.append(_blk("heading", para, level=lvl, page=n) if lvl else _blk("para", para, page=n))
        return out
    if ext == ".docx":
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        d = docx.Document(io.BytesIO(data))
        out = []
        for child in d.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                para = Paragraph(child, d)
                if not para.text.strip():
                    continue
                style = (para.style.name if para.style is not None else "") or ""
                m = re.match(r"^Heading (\d)$", style)
                if m or style == "Title":
                    out.append(_blk("heading", para.text.strip(), level=int(m.group(1)) if m else 1))
                else:
                    out.append(_blk("para", para.text.strip()))
            elif tag == "tbl":
                t = _md_table([[c.text for c in r.cells] for r in Table(child, d).rows])
                if t:
                    out.append(_blk("table", t))
        return out
    if ext in TEXT_EXT or mime.startswith("text/"):
        return markdown_blocks(data.decode("utf-8", errors="replace"))
    return markdown_blocks(extract_text(name, data, mime))


def extract_text(name: str, data: bytes, mime: str = "") -> str:
    ext = Path(name).suffix.lower()
    if ext == ".pdf" or mime == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((p.extract_text() or "") for p in reader.pages)
    if ext == ".docx":
        import docx

        # Tables included (the old paragraph-only join dropped them from read_document too).
        return "\n\n".join(b["text"] for b in extract_structured(name, data, mime) if b["kind"] != "page")
    if ext in TEXT_EXT or mime.startswith("text/"):
        return data.decode("utf-8", errors="replace")
    # Last resort: try utf-8
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"Unsupported file type: {ext or mime or 'unknown'}") from e
