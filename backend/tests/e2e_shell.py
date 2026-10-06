"""End-to-end: shell + file tools + undo in a granted folder, against a LIVE isolated backend.

    python backend/tests/e2e_shell.py <port> [scratch dir]

Token is e2e-<port> (see the e2e backend helper). Uses real models. Not collected by the offline suite.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8944
SCRATCH = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/grain-e2e-shell").resolve()
OUTSIDE = Path.home() / "Desktop" / "grain-e2e-should-fail.txt"
BASE = f"http://127.0.0.1:{PORT}"
H = {"Authorization": f"Bearer e2e-{PORT}"}
c = httpx.Client(base_url=BASE, headers=H, timeout=60)
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, evidence: str = "") -> None:
    results.append((name, bool(ok), evidence))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{evidence}]" if evidence else ""), flush=True)


def chat(cid: str, text: str, timeout: float = 420) -> dict:
    run_id = c.post(f"/conversations/{cid}/chat", json={"content": text}).json()["run_id"]
    events, cards, answered, since, done = [], [], set(), 0, False
    end = time.time() + timeout
    while not done and time.time() < end:
        try:
            with httpx.stream("GET", f"{BASE}/conversations/{cid}/stream", headers=H,
                              params={"since": since, "run_id": run_id}, timeout=httpx.Timeout(30, read=10)) as r:
                ev = None
                for line in r.iter_lines():
                    if line.startswith("id:"):
                        since = max(since, int(line[3:].strip() or 0))
                    elif line.startswith("event:"):
                        ev = line[6:].strip()
                    elif line.startswith("data:") and ev:
                        try:
                            d = json.loads(line[5:])
                        except ValueError:
                            d = line[5:]
                        events.append((ev, d))
                        if ev == "done" and not (isinstance(d, dict) and d.get("segment")):
                            done = True
                            break
        except httpx.ReadTimeout:
            pass
        if not done:
            rows = c.get("/approvals").json()
            rows = rows.get("approvals", rows) if isinstance(rows, dict) else rows
            for a in rows:
                k = str(a.get("call_id") or a.get("id"))
                if a.get("conversation_id") == cid and k not in answered:
                    c.post(f"/approvals/{k}", json={"decision": "allow"})
                    answered.add(k)
                    cards.append(a)
    conv = c.get(f"/conversations/{cid}").json()
    msgs = conv.get("messages", [])
    last = next((m for m in reversed(msgs) if m.get("role") == "assistant"), {})
    tools = [t for m in msgs for t in (m.get("tool_events") or [])]
    return {"done": done, "run_id": run_id, "text": last.get("content", ""), "msg_id": last.get("id"),
            "tools": tools, "events": events, "cards": cards}


def names(out: dict) -> list[str]:
    return [t.get("name") or t.get("tool") for t in out["tools"]]


def main() -> int:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    for p in SCRATCH.iterdir():
        if p.is_file():
            p.unlink()
    (SCRATCH / "data.csv").write_text("name,qty,price\napple,3,0.5\npear,2,0.75\nplum,4,0.25\n")
    (SCRATCH / "total.py").write_text(
        "import csv\n\ndef total(path):\n    s = 0\n    with open(path) as f:\n        for row in csv.DictReader(f):\n"
        "            s += int(row['qty']) + float(row['price'])  # BUG: should multiply\n    return s\n\n"
        "if __name__ == '__main__':\n    print(total('data.csv'))\n")
    OUTSIDE.unlink(missing_ok=True)
    c.put("/settings", json={"workspaceRoots": [str(SCRATCH)]})
    cid = c.post("/conversations", json={"title": "e2e shell"}).json()["id"]

    out = chat(cid, f"In the folder {SCRATCH} there is total.py with a bug and data.csv. Use fs_grep to find the bug "
                    "(it should multiply qty by price), fix it with fs_edit, run it with shell_run from that folder, "
                    "and write the printed result into out.txt in that folder. Then, as a separate shell_run, also try "
                    f"`touch {OUTSIDE}` and tell me what happened.")
    check("run finished", out["done"], out["run_id"])
    n = names(out)
    check("fs_grep used", "fs_grep" in n, str(n))
    check("fs_edit used", "fs_edit" in n, str(n))
    check("shell_run used", "shell_run" in n)
    src = (SCRATCH / "total.py").read_text()
    check("bug fixed in total.py", "+ float" not in src and "* float" in src, src.split("\n")[6])
    o = SCRATCH / "out.txt"
    good = False
    if o.exists():
        try:
            good = abs(float(o.read_text().strip().split()[-1]) - 4.0) < 1e-9
        except ValueError:
            good = False
    check("out.txt correct (4.0)", good, o.read_text()[:40] if o.exists() else "missing")
    check("outside file does NOT exist", not OUTSIDE.exists(), str(OUTSIDE))
    if OUTSIDE.exists():
        OUTSIDE.unlink()

    ch = c.get(f"/messages/{out['msg_id']}/changes").json()
    check("changes lists files", ch.get("count", 0) >= 2, json.dumps([f.get("path") for f in ch.get("files", [])]))
    rid = ch.get("run_id") or out["run_id"]
    u = c.post(f"/runs/{rid}/undo")
    check("undo ok", u.status_code == 200, u.text[:200])
    check("out.txt removed by undo", not o.exists())
    check("total.py restored", "+ float" in (SCRATCH / "total.py").read_text())
    r = c.post(f"/runs/{rid}/redo")
    check("redo ok", r.status_code == 200 and o.exists(), r.text[:120])
    c.post(f"/runs/{rid}/undo")

    cid2 = c.post("/conversations", json={"title": "e2e bg"}).json()["id"]
    out2 = chat(cid2, f"Use shell_run in {SCRATCH} with background=true to run `sleep 3 && echo done`. Then run a "
                      "separate foreground shell_run `sleep 6`. Do NOT call shell_poll. Afterwards, quote verbatim any "
                      "system notice you received about the background job finishing, including its job id and exit code.")
    n2 = names(out2)
    check("background job started", "shell_run" in n2 and "job_id" in json.dumps(out2["tools"]), str(n2))
    check("no shell_poll needed", "shell_poll" not in n2, str(n2))
    low = out2["text"].lower()
    check("completion note reached the run", out2["done"] and "finished" in low and "exit code 0" in low, out2["text"][:200])
    bad = [x for x in results if not x[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
