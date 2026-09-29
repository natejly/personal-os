"""End-to-end check for conversational recall.

Buries a distinctive fact in turn 1, fills the transcript until that turn is pushed out of the
resent window, then asks for the fact back. Passes only if the answer contains it *and* the reply
reports having recalled an earlier turn — i.e. it came from the index, not from the transcript.

  TOKEN=$(cat "$HOME/Library/Application Support/personal-os/data/.auth_token") \
  PORT=8765 MODEL=deepseek-v4-flash backend/.venv/bin/python scripts/check-recall.py
"""
import json, os, sys, urllib.request

BASE = f"http://127.0.0.1:{os.environ.get('PORT', '8765')}"
TOKEN = os.environ["TOKEN"]
MODEL = os.environ.get("MODEL", "deepseek-v4-flash")

SECRET = "kv/prod/atlas-7742"
FACT = f"Remember this for later: my deploy key lives at the vault path {SECRET}. Just acknowledge briefly."
FILLER = [
    "In one short sentence, what is a monad?",
    "In one short sentence, why is the sky blue?",
    "In one short sentence, what does a load balancer do?",
    "In one short sentence, what is a B-tree?",
    "In one short sentence, what is TCP slow start?",
    "In one short sentence, what is a bloom filter?",
]
ASK = "What was the vault path I told you my deploy key lives at? Answer with the path only."


def req(path, payload=None, method=None, stream=False):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method or ("POST" if data else "GET"),
                               headers={"x-personal-os-token": TOKEN, "Content-Type": "application/json"})
    resp = urllib.request.urlopen(r, timeout=600)
    return resp if stream else json.loads(resp.read())


def say(conv_id, text):
    """One turn. Returns (assistant_text, context_used)."""
    out, ctx, event = [], {}, None
    for raw in req(f"/conversations/{conv_id}/chat", {"content": text, "model": MODEL}, stream=True):
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("event:"):
            event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        d = json.loads(line[5:].strip())
        if event == "delta":
            out.append(d["text"])
        elif event == "assistant_message":
            ctx = d.get("context_used") or {}
        elif event == "done":
            ctx = d.get("context_used") or ctx
    return "".join(out), ctx


conv = req("/conversations", {"project_id": None, "title": "recall check", "model": MODEL})
cid = conv["id"]
# Per-conversation so the check never touches the user's global settings. A tiny budget forces the
# opening turn out of the window after a few exchanges; tools off keeps the filler turns fast.
req(f"/conversations/{cid}", {"settings": {"maxHistoryTokens": 220, "recallTurns": 4,
                                          "useRecall": True, "useTools": False, "autoLearn": False}},
    method="PATCH")
print(f"conversation {cid} model={MODEL} budget=220 tokens")

say(cid, FACT)
print("turn 1: fact planted")
for i, q in enumerate(FILLER, start=2):
    _, ctx = say(cid, q)
    print(f"turn {i}: filler (dropped={ctx.get('history_dropped', 0)}, recalled={len(ctx.get('recalled') or [])})")

answer, ctx = say(cid, ASK)
dropped = ctx.get("history_dropped", 0)
recalled = ctx.get("recalled") or []
prompt = ctx.get("system_prompt") or ""
print(f"\nfinal turn: dropped={dropped} recalled={len(recalled)}")
for h in recalled:
    print(f"  recalled: {h['summary'][:80]}")
print(f"answer: {answer.strip()[:200]}")

fails = []
if dropped == 0:
    fails.append("nothing was dropped from the window, so recall was never exercised")
if not recalled:
    fails.append("no earlier turn was recalled")
if SECRET not in prompt:
    fails.append("the recalled block did not carry the fact into the prompt")
if SECRET not in answer:
    fails.append(f"answer did not contain {SECRET}")
print("\nFAIL: " + "; ".join(fails) if fails else "\nPASS")
sys.exit(1 if fails else 0)
