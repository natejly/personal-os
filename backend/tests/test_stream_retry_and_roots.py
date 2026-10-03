"""A rate-limited model route is retried before any token streams, and a workspace folder the file tools
would refuse is rejected by PUT /settings instead of being silently ignored. Offline.

Run: python backend/tests/test_stream_retry_and_roots.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
HOME = Path(tempfile.mkdtemp(prefix="roots-home-")).resolve()
os.environ["HOME"] = str(HOME)
os.environ.setdefault("PERSONAL_OS_DATA_DIR", tempfile.mkdtemp(prefix="roots-data-"))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from personal_os import llm  # noqa: E402
from personal_os.app import AUTH_TOKEN, app  # noqa: E402

SSE = b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'


def run_stream(statuses: list[int]) -> tuple[list[dict], int]:
    seen = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = seen["n"]
        seen["n"] += 1
        code = statuses[i] if i < len(statuses) else 200
        if code != 200:
            return httpx.Response(code, headers={"retry-after": "0"}, content=b'{"error":"slow down"}')
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=SSE)

    real = httpx.AsyncClient

    class Mocked(real):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):  # type: ignore[no-untyped-def]
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    async def go() -> list[dict]:
        return [ev async for ev in llm.stream_chat({"baseUrl": "http://model.test"}, "m", [{"role": "user", "content": "x"}])]

    httpx.AsyncClient = Mocked  # type: ignore[misc]
    try:
        try:
            return asyncio.run(go()), seen["n"]
        except llm.LLMError as e:
            return [{"type": "error", "text": str(e)}], seen["n"]
    finally:
        httpx.AsyncClient = real  # type: ignore[misc]


def main() -> None:
    events, calls = run_stream([429, 503])
    text = "".join(e.get("text", "") for e in events if e.get("type") == "delta")
    assert calls == 3 and text == "hi", f"two refusals then a stream: retried and streamed once ({calls}, {events})"
    rt = [e for e in events if e["type"] == "retry"]
    assert [(e["attempt"], e["reason"]) for e in rt[:2]] == [(1, "rate_limit"), (2, "provider_error")] \
        and rt[0]["max"] == llm.DEFAULT_SETTINGS["llmRetries"], rt
    assert rt[2] == {"type": "retry", "attempt": 0}, "a clear follows the successful retry"
    kinds = [e["type"] for e in events]
    assert kinds.index("delta") > max(i for i, k in enumerate(kinds) if k == "retry"), "every retry event precedes the first token"
    assert not [e for e in run_stream([])[0] if e["type"] == "retry"], "no retry events without a retry"
    events, calls = run_stream([429] * 10)
    retries = llm.DEFAULT_SETTINGS["llmRetries"]
    assert calls == retries + 1 and events[0]["type"] == "error" and "429" in events[0]["text"], \
        f"a route that keeps refusing surfaces the error after {retries} retries ({calls}, {events})"
    events, calls = run_stream([400])
    assert calls == 1 and events[0]["type"] == "error", "a 400 is not retried"

    client = TestClient(app, headers={"X-Personal-OS-Token": AUTH_TOKEN})
    (HOME / "projects").mkdir()
    (HOME / ".hidden" / "work").mkdir(parents=True)
    ok = client.put("/settings", json={"workspaceRoots": [str(HOME / "projects")]})
    assert ok.status_code == 200 and ok.json()["workspaceRoots"] == [str(HOME / "projects")], ok.text
    bad = client.put("/settings", json={"workspaceRoots": [str(HOME / ".hidden" / "work")]})
    assert bad.status_code == 422 and "hidden" in bad.text, f"a root inside a hidden folder is rejected: {bad.status_code} {bad.text}"
    outside = client.put("/settings", json={"workspaceRoots": ["/etc"]})
    assert outside.status_code == 422 and "home folder" in outside.text, outside.text
    assert client.get("/settings").json()["workspaceRoots"] == [str(HOME / "projects")], "a rejected update changes nothing"
    print("test_stream_retry_and_roots: ok")


if __name__ == "__main__":
    main()
