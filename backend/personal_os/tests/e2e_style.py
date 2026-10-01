"""End-to-end check of the writing-style feature against the real FastAPI app, with the LLM stubbed.

Run: backend/.venv/bin/python backend/personal_os/tests/e2e_style.py

A script, not a unittest: it imports `personal_os.app`, which builds the whole app (one database, one
toolbox) at import time against PERSONAL_OS_DATA_DIR, so it cannot share a process with the rest of
the suite. `unittest discover` only collects test*.py, so this file stays out of it deliberately.

It covers what test_style.py cannot: the HTTP surface, scope handling across projects, a real chat
turn banking a sample and streaming `style_learned`, and the two tools.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["PERSONAL_OS_DATA_DIR"] = tmp
os.environ["PERSONAL_OS_AUTH_TOKEN"] = "test-token"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi.testclient import TestClient  # noqa: E402

from personal_os import app as mod  # noqa: E402

PROFILE = {
    "summary": "Writes in short, direct sentences and opens with the ask.",
    "guidelines": ["open with the ask", "contractions throughout"],
    "traits": {"sentence length": "short, 8-14 words", "formality": "low"},
    "phrases": ["quick one"],
    "avoid": ["corporate jargon"],
}
PROSE = (
    "I spent the morning rewriting the onboarding email. The old one opened with three sentences of "
    "preamble before it got anywhere near the point, which is the sort of thing I keep telling everyone "
    "else not to do. The new one asks for the thing in the first line and explains why afterwards."
)


async def fake_complete(settings, model, messages, kind="learn"):
    if kind == "style":
        return json.dumps(PROFILE)
    return '{"memories": [], "entities": [], "relations": []}'


async def fake_stream(settings, model, messages, tools=None, kind="chat", effort="default", tool_choice="auto"):
    yield {"type": "delta", "text": "Noted."}
    yield {"type": "end", "finish_reason": "stop", "tool_calls": [], "usage": None,
           "usage_est": {"prompt_tokens": 10, "completion_tokens": 2}}


import personal_os.style as style_mod  # noqa: E402

mod.llm.complete = fake_complete
mod.llm.stream_chat = fake_stream
style_mod.llm.complete = fake_complete  # style.py holds its own reference to the llm module

c = TestClient(mod.app, headers={"X-Personal-OS-Token": "test-token"})
fails: list[str] = []


def check(label: str, cond: bool, extra: object = "") -> None:
    print(("PASS  " if cond else "FAIL  ") + label + ("" if cond else f"  -> {extra!r}"))
    if not cond:
        fails.append(label)


# 1. Empty state
r = c.get("/style").json()
check("empty scope has no profile", r["profile"] is None and r["stats"]["samples"] == 0, r)

# 2. Learning with no samples is refused, not a 500
r = c.post("/style/learn", json={})
check("learn with no samples -> 400", r.status_code == 400, r.text)

# 3. Add a sample by hand, then learn
r = c.post("/style/samples", json={"text": PROSE})
check("sample added by hand", r.status_code == 200 and r.json()["source"] == "paste", r.text)
r = c.post("/style/learn", json={}).json()
check("profile learned", r["profile"]["summary"] == PROFILE["summary"], r)
check("guidelines stored as a list", r["profile"]["guidelines"] == PROFILE["guidelines"], r["profile"])
check("samples folded in", r["stats"]["pending"] == 0, r["stats"])

# 4. Hand edit marks `edited` and survives
r = c.put("/style", json={"guidelines": ["say it in one line"]}).json()
check("hand edit saved", r["profile"]["guidelines"] == ["say it in one line"], r["profile"])
check("hand edit sets edited", r["profile"]["edited"] == 1, r["profile"])
r = c.put("/style", json={"enabled": False}).json()
check("toggling enabled keeps the text", r["profile"]["guidelines"] == ["say it in one line"], r["profile"])
c.put("/style", json={"enabled": True})

# 5. Project scope: inherits, then overrides
p = c.post("/projects", json={"name": "Work"}).json()
r = c.get(f"/style?project_id={p['id']}").json()
check("project inherits the personal voice", r["profile"] is None and r["inherited"] and r["effective"], r)
c.post("/style/samples", json={"project_id": p["id"], "text": PROSE})
r = c.post("/style/learn", json={"project_id": p["id"]}).json()
check("project gets its own voice", r["profile"] is not None and not r["inherited"], r)
r = c.put("/style", json={"project_id": "nope", "summary": "x"})
check("unknown project -> 404", r.status_code == 404, r.text)

# 6. Injection into a chat's context
conv = c.post("/conversations", json={"project_id": None, "model": "m"}).json()
ctx = c.post("/context/preview", json={"query": "draft an email to Sam"}).json()
check("style block injected", "How the user writes" in ctx["system_prompt"], ctx["system_prompt"][:200])
check("style recorded in context_used", ctx["style"] and ctx["style"]["guidelines"] == ["say it in one line"], ctx.get("style"))
ctx = c.post("/context/preview", json={"query": "draft an email", "conv_settings": {"useStyle": False}}).json()
check("chat can opt out", "How the user writes" not in ctx["system_prompt"] and ctx["style"] is None, ctx.get("style"))

# 7. A chat turn banks prose and relearns; a short instruction does not
before = c.get("/style").json()["stats"]["samples"]


def send(text: str) -> list[str]:
    c.post(f"/conversations/{conv['id']}/chat", json={"content": text})
    events: list[str] = []
    with c.stream("GET", f"/conversations/{conv['id']}/stream?since=0") as s:
        for line in s.iter_lines():
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
            if line.strip() == "event: run_end":
                break
    return events


events = send("fix the typo")
check("short instruction banks nothing", c.get("/style").json()["stats"]["samples"] == before,
      c.get("/style").json()["stats"])
check("no style event for an instruction", "style_learned" not in events, events)

events = send(PROSE + " It reads like a person wrote it now.")
after = c.get("/style").json()
check("prose message is banked", after["stats"]["samples"] == before + 1, after["stats"])
check("style_learned streamed", "style_learned" in events, events)
msgs = c.get(f"/conversations/{conv['id']}").json()["messages"]
spans = [s["kind"] for m in msgs if m.get("trace") for s in m["trace"]]
check("trace has a style span", "style" in spans, spans)

# 8. A doc the user saves becomes a sample
doc = c.post("/docs", json={"title": "Note", "content": ""}).json()
c.put(f"/docs/{doc['id']}", json={"content": PROSE, "title": "Note", "summary": ""})
samples = c.get("/style/samples").json()
check("doc save banked once", sum(1 for s in samples if s["ref"] == f"doc:{doc['id']}") == 1, [s["ref"] for s in samples])
c.put(f"/docs/{doc['id']}", json={"content": PROSE + " And one more line about it.", "title": "Note", "summary": ""})
samples = c.get("/style/samples").json()
check("doc re-save refreshes, not duplicates", sum(1 for s in samples if s["ref"] == f"doc:{doc['id']}") == 1,
      [s["ref"] for s in samples])

# 9. learnStyle=false stops the banking
c.put("/settings", json={"learnStyle": False})
n = c.get("/style").json()["stats"]["samples"]
send(PROSE + " Another passage entirely, written later.")
check("learnStyle off banks nothing", c.get("/style").json()["stats"]["samples"] == n, c.get("/style").json()["stats"])
c.put("/settings", json={"learnStyle": True})

# 10. The tools see the profile
async def call_tool(name, args):
    return await mod.toolbox.specs[name].fn({"project_id": None, "conversation_id": conv["id"]}, **args)


out = asyncio.run(call_tool("writing_style", {}))
check("writing_style tool reads the profile", out["guidelines"] == ["say it in one line"], out)
out = asyncio.run(call_tool("save_writing_sample", {"text": "Hey — quick one. Pushed the launch to Tuesday."}))
check("save_writing_sample tool banks", "saved" in out, out)

# 11. Delete paths
r = c.delete("/style/samples/" + c.get("/style/samples").json()[0]["id"])
check("sample deleted", r.status_code == 200, r.text)
r = c.delete("/style?with_samples=true").json()
check("reset clears profile and samples", r["profile"] is None and r["stats"]["samples"] == 0, r)
r = c.get(f"/style?project_id={p['id']}").json()
check("project voice survives a personal reset", r["profile"] is not None, r)

print()
print("FAILED:", fails if fails else "none")
sys.exit(1 if fails else 0)
