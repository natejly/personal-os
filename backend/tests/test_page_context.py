"""The page block the ⌘I agent sends. Run: PERSONAL_OS_DATA_DIR=/tmp/x python backend/tests/test_page_context.py"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="pagectxtest-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.app import ChatIn  # noqa: E402
from personal_os.context import PAGE_DETAIL_LIMIT, build_context, page_block  # noqa: E402

passed = 0


def check(cond: Any, label: str) -> None:
    global passed
    assert cond, label
    passed += 1


class _Repo:
    """The three retrieval repos, all answering "nothing", so only the page block is under test."""

    def for_context(self, *_a: Any, **_k: Any) -> list[Any]:
        return []

    def neighborhood(self, *_a: Any, **_k: Any) -> dict[str, list[Any]]:
        return {"nodes": [], "edges": []}

    def search(self, *_a: Any, **_k: Any) -> list[Any]:
        return []


def build(page: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    repo = _Repo()
    return build_context(
        memories=repo, graph=repo, documents=repo, project=None, project_id=None,
        query="what does this say?", settings={}, conv_settings={}, global_system_prompt="You are Grain.",
        page=page,
    )


# ---- the block itself ----
block = page_block({
    "view": "docs", "label": 'Doc "Weekly notes"', "detail": "# Weekly notes\n\nShip the thing.",
    "selection": "Ship the thing.",
    "refs": [{"kind": "doc", "id": "doc_1", "name": "Weekly notes"}],
})
check('Doc "Weekly notes"' in block, "the label names the screen")
check("doc_1" in block and "Weekly notes" in block, "a ref carries its id, so the model can act on it by id")
check("Ship the thing." in block, "the selection rides along")
check("# Weekly notes" in block, "the screen contents are included")

check(page_block({"label": "", "detail": "orphan"}) == "", "a page with no label says nothing")

long = page_block({"label": "Doc", "detail": "x" * (PAGE_DETAIL_LIMIT + 5000)})
check(len(long) < PAGE_DETAIL_LIMIT + 500 and "truncated" in long, f"a long page is clipped: {len(long)}")

# ---- and how it lands in the system prompt ----
system, used = build({"view": "todos", "label": "Todos", "detail": "- ship it (`t1`)", "refs": []})
check("What the user is looking at" in system, "the block is in the system prompt")
check("ship it" in system, "with the screen's own rows")
check(used["page"] is not None and used["page"]["label"] == "Todos", "and is reported back as context used")
check(used["tokens_estimate"] > 0, "the estimate covers it")

system, used = build(None)
check("What the user is looking at" not in system, "an ordinary chat turn carries no page block")
check(used["page"] is None, "and reports none")

# ---- the request model accepts one, and still accepts a turn without one ----
body = ChatIn(content="summarise this", page_context={"view": "docs", "label": "Doc", "detail": "hi"})
check(body.page_context is not None and body.page_context.label == "Doc", "ChatIn parses a page context")
check(ChatIn(content="hello").page_context is None, "page_context stays optional")

print(f"test_page_context: {passed} checks passed")
