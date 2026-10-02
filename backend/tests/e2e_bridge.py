"""Live end-to-end: programmatic tool calling, agent-started desks, unattended shell refusal.

    python backend/tests/e2e_bridge.py <port> [scratch dir]

Needs a backend started with real models (token e2e-<port>). Not run by the offline suite.
Sets workspaceRoots to the scratch folder, so run it against an isolated data dir only.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.environ.get("E2E_HELPER_DIR", "/Users/natejly/.claude/jobs/0e923301/tmp"))
from e2e import Grain  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8946
SCRATCH = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/e2e-bridge").resolve()
OUTSIDE = Path("/tmp/grain-e2e-bridge-must-not-exist.txt")
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, evidence: str = "") -> None:
    results.append((name, ok, evidence))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{evidence[:300]}]" if evidence else ""), flush=True)


def tool_names(tools: list) -> list[str]:
    return [t.get("name", "") for t in tools]


def main() -> int:
    g = Grain(PORT)
    ws = SCRATCH / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    expected: dict[str, list[str]] = {}
    for i in range(10):
        lines = [f"line one of {i}", "plain code"]
        if i % 3 != 2:
            lines.append(f"# TODO: item-{i}-a")
        if i % 4 == 0:
            lines.append(f"// TODO fix-{i}-b")
        (ws / f"f{i}.txt").write_text("\n".join(lines) + "\n")
        expected[f"f{i}.txt"] = [ln for ln in lines if "TODO" in ln]
    total = sum(len(v) for v in expected.values())
    g.put("/settings", {**g.get("/settings"), "workspaceRoots": [str(ws)], "unattendedApprovals": "deny"})

    # 1. one run_python call using the bridge
    names: list[str] = []
    out: dict = {}
    for _ in range(3):
        cid = g.new_chat("bridge")
        out = g.chat(cid, f"Use one run_python call with tools=['fs_grep','fs_glob'] to find every TODO under {ws} and print them "
                          "grouped by file. Do not use any other tool.", approve=lambda c: "allow", timeout=420)
        names = tool_names(out["tools"])
        if "run_python" in names:
            break
    check("single run_python call", names.count("run_python") == 1, str(names))
    rp = next((t for t in out["tools"] if t.get("name") == "run_python"), {})
    print("run_python event:", json.dumps(rp)[:1500])
    blob = json.dumps(rp) + out["text"]
    found = sum(1 for v in expected.values() for ln in v if ln.split("TODO")[1].strip(": ").strip() in blob)
    check("all TODO lines present in output", found == total, f"{found}/{total}")
    check("bridge used (fs_* via run_python, no direct fs_grep)", "fs_grep" in json.dumps(rp) and "fs_grep" not in names, str(names))

    # 2. desk_start raises a card, desk appears in plan mode
    before = {d["id"] for d in g.get("/cowork/desks")}
    cid = g.new_chat("desk")
    cards: list[dict] = []

    def appr(card):
        cards.append(card)
        return "allow"
    g.chat(cid, "Start a cowork desk with the desk_start tool, title 'Notes tidy', brief: 'Write a one-line file hello.txt "
                "in your workspace.' Then just tell me it started.", approve=appr, timeout=420)
    check("desk_start approval card raised", any("desk_start" in json.dumps(c) for c in cards), json.dumps(cards)[:300])
    new = [d for d in g.get("/cowork/desks") if d["id"] not in before]
    check("desk exists after approval", len(new) == 1, str([d["id"] for d in new]))
    if new:
        d = new[0]
        check("desk is in plan mode", d.get("autonomy") == "plan", f"autonomy={d.get('autonomy')} status={d.get('status')}")
        time.sleep(1)
        g.post(f"/cowork/desks/{d['id']}/stop")

    # 3. scheduled once job that tries shell_run is refused
    job = g.post("/jobs", {"name": "bridge shell", "kind": "once", "run_at": time.time() + 8, "enabled": True,
                           "prompt": f"Call shell_run with command: echo hi > {OUTSIDE}  and report what happened."})
    row = None
    for _ in range(90):
        time.sleep(2)
        runs = g.get(f"/jobs/{job['id']}/runs")
        if runs and runs[0].get("status") not in ("running", "queued", None):
            row = runs[0]
            break
    check("job ran", row is not None, json.dumps(row)[:300])
    if row:
        conv = g.get(f"/conversations/{row['conversation_id']}") if row.get("conversation_id") else {}
        tev = [t for m in conv.get("messages", []) for t in (m.get("tool_events") or [])]
        sh = [t for t in tev if t.get("name") == "shell_run"]
        print("shell events:", json.dumps(sh)[:800])
        check("shell_run was refused or not executed", "hi" not in json.dumps([t.get("result") for t in sh]) or not sh, json.dumps(sh)[:300])
    check("outside probe file does not exist", not OUTSIDE.exists(), str(OUTSIDE))
    if OUTSIDE.exists():
        OUTSIDE.unlink()
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
