"""Exact model requests, one JSONL file per reply under <data>/requests (a line per round). Bodies only, never headers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context import estimate_tokens

KEEP = 50  # replies kept; older files are pruned on write


def record(data_dir: Path, message_id: str, rnd: int, model: str, messages: list[Any], tools: list[Any] | None,
           tool_choice: str) -> None:
    body = json.dumps({"messages": messages, "tools": tools or []}, default=str)
    line = json.dumps({"round": rnd, "model": model, "tool_choice": tool_choice, "tokens": estimate_tokens(body),
                       **json.loads(body)})
    d = data_dir / "requests"
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"{message_id}.jsonl").open("a") as f:
        f.write(line + "\n")
    for old in sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)[:-KEEP]:
        old.unlink(missing_ok=True)


def read(data_dir: Path, message_id: str) -> list[dict[str, Any]] | None:
    p = data_dir / "requests" / f"{Path(message_id).name}.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x] if p.is_file() else None
