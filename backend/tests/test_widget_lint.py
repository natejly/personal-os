"""Widget HTML lint + one-round repair (dashboards.py). Offline. Run: backend/.venv/bin/python backend/tests/test_widget_lint.py"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="widgetlint-"))
os.environ.setdefault("PERSONAL_OS_AUTH_TOKEN", "test-token")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import dashboards as d, llm  # noqa: E402

BASE = "http://127.0.0.1:9"
SRC = [{"id": "s1", "name": "N", "kind": "x", "description": ""}]
GOOD = f"<!doctype html><html><body><div id=a></div><script>fetch('{BASE}/sources/s1/fetch')</script></body></html>"
BAD = "<!doctype html><html><body><script src='https://cdn.x/a.js'></script><script>fetch('" + BASE + "/sources/s1/fetch')</script></body></html>"
REPLIES: list[str] = []
CALLS: list[int] = []


async def fake(settings, model, messages, **kw):
    CALLS.append(1)
    return REPLIES.pop(0)

llm.complete = fake  # type: ignore[assignment]


def run(*replies):
    REPLIES[:] = replies
    CALLS.clear()
    return asyncio.run(d.generate_widget_code({}, "m", "p", SRC, BASE, 2, 200, {}))

assert d.lint_widget_html(GOOD, BASE, True) == []
assert d.lint_widget_html("<html><body>  </body></html>") == ["the body is empty"]
assert any("fetch" in i for i in d.lint_widget_html("<html><body>hi</body></html>", BASE, True))
assert run(GOOD) == GOOD and len(CALLS) == 1
assert run(BAD, GOOD) == GOOD and len(CALLS) == 2
assert run(BAD, BAD) == BAD and len(CALLS) == 2
assert d.lint_widget_html(BAD, BASE, True)
print("ok")
