"""End-to-end check: a reasoning model's chain-of-thought reaches the client as `reasoning`
SSE events well before the first `delta`, and is persisted on the message."""
import json, os, sys, time, urllib.request

BASE = f"http://127.0.0.1:{os.environ.get('PORT', '8790')}"
TOKEN = os.environ["TOKEN"]
MODEL = os.environ.get("MODEL", "kimi-k3")


def call(path, payload=None, method=None, stream=False):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"x-personal-os-token": TOKEN, "Content-Type": "application/json"})
    r = urllib.request.urlopen(req, timeout=180)
    return r if stream else json.loads(r.read())


conv = call("/conversations", {"project_id": None, "title": "reasoning e2e", "model": MODEL})
print(f"conversation {conv['id']} model={conv['model']}")

t0 = time.time()
first_reason = first_delta = None
reason_chars = delta_chars = 0
msg_id = None
event = None
resp = call(f"/conversations/{conv['id']}/chat", {"content": "What is 2+2? Answer in one short sentence.", "model": MODEL}, stream=True)
for raw in resp:
    line = raw.decode("utf-8", "replace").strip()
    if line.startswith("event:"):
        event = line[6:].strip()
        continue
    if not line.startswith("data:"):
        continue
    d = json.loads(line[5:].strip())
    if event == "assistant_message":
        msg_id = d["id"]
    elif event == "reasoning":
        reason_chars += len(d["text"])
        if first_reason is None:
            first_reason = time.time() - t0
    elif event == "delta":
        delta_chars += len(d["text"])
        if first_delta is None:
            first_delta = time.time() - t0
    elif event == "done":
        print(f"done: persisted_reasoning={len(d.get('reasoning') or '')} chars")

print(f"reasoning: {reason_chars} chars, first @ {first_reason if first_reason else -1:.2f}s")
print(f"content:   {delta_chars} chars, first @ {first_delta if first_delta else -1:.2f}s")

# Reload from the DB: reasoning must survive, and must not be mixed into content.
again = call(f"/conversations/{conv['id']}")
m = [x for x in again["messages"] if x["id"] == msg_id][0]
print(f"after reload: reasoning={len(m.get('reasoning') or '')} chars, content={len(m['content'])} chars")

fails = []
if reason_chars == 0:
    fails.append("no reasoning events received")
if delta_chars == 0:
    fails.append("no content received")
if first_reason is not None and first_delta is not None and first_reason > first_delta:
    fails.append("reasoning arrived after content")
if not (m.get("reasoning") or ""):
    fails.append("reasoning not persisted")
if (m.get("reasoning") or "")[:40] and (m.get("reasoning") or "")[:40] in m["content"]:
    fails.append("reasoning leaked into content")
print("FAIL: " + "; ".join(fails) if fails else "PASS")
sys.exit(1 if fails else 0)
