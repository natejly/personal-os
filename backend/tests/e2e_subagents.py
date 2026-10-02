"""End-to-end check of subagent fan-out against a LIVE backend (not run by the offline suite).

    python backend/tests/e2e_subagents.py <port> [token]      (E2E_WORK=<scratch dir> to choose the work folder)

The backend must be an isolated one (a data dir you can throw away) with a working model. The script sets
workspaceRoots to a scratch folder, asks a chat to fan out three researchers, then checks the child runs,
their overlap in time, the wrapped reports, the summary file, and that a tool turned off for the chat is
absent from every child's tool list. Exit status is 0 when every check passes.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx

TOPICS = ["SQLite WAL mode", "Python asyncio cancellation", "HTTP/3"]
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, evidence: str = "") -> bool:
    results.append((name, bool(ok), evidence))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{evidence}]" if evidence else ""))
    return bool(ok)


class Client:
    def __init__(self, port: int, token: str):
        self.c = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"}, timeout=60)

    def get(self, p: str):
        r = self.c.get(p); r.raise_for_status(); return r.json()

    def post(self, p: str, body=None):
        r = self.c.post(p, json=body); r.raise_for_status(); return r.json() if r.content else None

    def put(self, p: str, body=None):
        r = self.c.put(p, json=body); r.raise_for_status(); return r.json() if r.content else None

    def patch(self, p: str, body=None):
        r = self.c.patch(p, json=body); r.raise_for_status(); return r.json()

    def run_chat(self, cid: str, text: str, timeout: float = 1200) -> str:
        """Send a message, allow every approval card of this chat, return the run id once the run is over."""
        run_id = self.post(f"/conversations/{cid}/chat", {"content": text})["run_id"]
        deadline = time.time() + timeout
        while time.time() < deadline:
            rows = self.get("/approvals")
            rows = rows.get("approvals", rows) if isinstance(rows, dict) else rows
            for a in rows:
                if a.get("conversation_id") == cid:
                    self.post(f"/approvals/{a.get('call_id') or a.get('id')}", {"decision": "allow"})
            if self.get(f"/runs/{run_id}").get("status") not in ("running", "waiting", "queued"):
                break
            time.sleep(2)
        return run_id


def main() -> int:
    port = int(sys.argv[1])
    token = sys.argv[2] if len(sys.argv) > 2 else f"e2e-{port}"
    g = Client(port, token)
    work = Path(os.environ.get("E2E_WORK") or tempfile.mkdtemp(prefix="e2e-subagents-")) / "work"
    work.mkdir(parents=True, exist_ok=True)
    summary = work / "summary.md"
    summary.unlink(missing_ok=True)
    # Three web researchers plus the summary outrun the default run clock (300 s), run cost ($0.50) and
    # per-child cap ($0.25), which would end the parent before it can write the file; raise them for the test.
    g.put("/settings", {"workspaceRoots": [str(work)], "maxRunSeconds": 1500, "maxRunCost": 5, "subagentMaxCost": 1.0})

    cid = g.post("/conversations", {"title": "e2e subagents"})["id"]
    prompt = ("Use agent_spawn to start three researchers in parallel (background=true), one per topic: "
              + ", ".join(TOPICS) + f". Wait for their reports with agent_wait, then write a combined summary to {summary} "
              "with write_local_file (or shell_run if that path is refused) that has a section for each topic.")
    run_id = g.run_chat(cid, prompt)
    run = g.get(f"/runs/{run_id}")
    check("parent run finished", run.get("status") in ("done", "completed", "succeeded"), str(run.get("status")))
    kids = g.get(f"/runs/{run_id}/children")
    check("three child runs", len(kids) >= 3, f"{len(kids)} children: {[k['run_id'] for k in kids]}")
    check("children link to parent", bool(kids) and all(k.get("parent_run_id") == run_id for k in kids))
    spans = [(k["started_at"], k.get("ended_at") or time.time()) for k in kids]
    overlap = any(a[0] < b[1] and b[0] < a[1] for i, a in enumerate(spans) for b in spans[i + 1:])
    check("children overlapped in time", overlap, str([(round(s - spans[0][0], 1), round(e - spans[0][0], 1)) for s, e in spans]))
    conv = g.get(f"/conversations/{cid}")
    tool_events = [t for m in conv["messages"] for t in (m.get("tool_events") or [])]
    waits = json.dumps([t for t in tool_events if t.get("name") == "agent_wait"])
    # tool_events keep only a preview of each result, so one visible wrapper proves the shape.
    check("parent received wrapped reports", "subagent id=" in waits and "data from another agent" in waits, f"wrapper count in previews={waits.count('subagent id=')}")
    check("summary.md exists", summary.exists(), str(summary))
    text = summary.read_text().lower() if summary.exists() else ""
    for key in ("wal", "asyncio", "http/3"):
        check(f"summary covers {key}", key in text)

    # A tool turned off for the chat is absent from every child's tool list.
    cid2 = g.post("/conversations", {"title": "e2e subagents off"})["id"]
    g.patch(f"/conversations/{cid2}", {"settings": {"tools": {"web_search": "off"}}})
    run2 = g.run_chat(cid2, "Use agent_spawn to start one researcher to answer: what is SQLite WAL mode? Give a two sentence report.")
    kids2 = g.get(f"/runs/{run2}/children")
    check("second chat spawned a child", len(kids2) >= 1, str(len(kids2)))
    for k in kids2:
        offered = (k.get("input") or {}).get("tools") or []
        check("child lacks web_search the parent has off", "web_search" not in offered and bool(offered), f"offered={offered}")
        used = {e["data"].get("name") for e in g.get(f"/runs/{k['run_id']}/events") if e["event"] == "tool_call"}
        check("child never called web_search", "web_search" not in used, str(sorted(used)))
    failed = [r for r in results if not r[1]]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
