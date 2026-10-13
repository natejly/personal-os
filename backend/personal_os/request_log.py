"""Exact model requests, one JSONL file per reply under <data>/requests (a line per round). Bodies only, never headers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context import estimate_tokens

KEEP = 50  # replies kept; older files are pruned on write


def _part(p: Any) -> Any:
    """An attached picture's data: URL as a size placeholder; it is replayed every round and would be megabytes a line."""
    url = (p.get("image_url") or {}).get("url") if isinstance(p, dict) and p.get("type") == "image_url" and isinstance(p.get("image_url"), dict) else None
    if isinstance(url, str) and url.startswith("data:"):
        return {**p, "image_url": {**p["image_url"], "url": f"<image {len(url)} bytes>"}}
    return p


def record(data_dir: Path, message_id: str, rnd: int, model: str, messages: list[Any], tools: list[Any] | None,
           tool_choice: str) -> None:
    msgs = [{**m, "content": [_part(p) for p in m["content"]]} if isinstance(m, dict) and isinstance(m.get("content"), list) else m
            for m in messages]
    body = json.dumps({"messages": msgs, "tools": tools or []}, default=str)
    d = data_dir / "requests"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{message_id}.jsonl"
    new = not path.exists()
    with path.open("a") as f:
        # The body is serialised once; the small header is spliced onto it rather than the whole thing re-parsed.
        head = json.dumps({"round": rnd, "model": model, "tool_choice": tool_choice, "tokens": estimate_tokens(body)})
        f.write(head[:-1] + ", " + body[1:] + "\n")
    if new:  # prune once per reply, not once per round
        for old in sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)[:-KEEP]:
            old.unlink(missing_ok=True)


def read(data_dir: Path, message_id: str) -> list[dict[str, Any]] | None:
    p = data_dir / "requests" / f"{Path(message_id).name}.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x] if p.is_file() else None
