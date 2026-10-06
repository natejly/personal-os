"""End-to-end check of workflows + commands against a LIVE isolated backend (real models).

    python backend/tests/e2e_workflows.py <port> [scratch dir] [--kill-cmd CMD --start-cmd CMD]

Needs a backend started with an isolated data dir whose auth token is e2e-<port> and a working model. The agent
only ever writes inside the scratch folder (set as the sole workspaceRoots entry). Exit code 1 on any failed check.
The interruption phase runs only when --kill-cmd (stops the backend) and --start-cmd (starts it again, returning
once it answers) are both given; it kills the backend mid fan-out, restarts it, resumes the run and checks that
steps that were already done did not run again.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.environ.get("E2E_HELPER_DIR", "/Users/natejly/.claude/jobs/0e923301/tmp"))
from e2e import Grain  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, evidence: str = "") -> bool:
    CHECKS.append((name, bool(ok), evidence))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{evidence}]" if evidence else ""), flush=True)
    return bool(ok)


FILES = {
    "alpha.md": "# Alpha\nThe alpha project ships a tiny CLI that renames photos by EXIF date.\n",
    "bravo.md": "# Bravo\nBravo is a recipe collection: sourdough, miso soup and a lemon tart.\n",
    "charlie.md": "# Charlie\nCharlie tracks hiking trips; next one is Mount Rainier in August.\n",
    "cedar.md": "# Cedar\nCedar notes a plan to migrate the home server from Debian 11 to Debian 12.\n",
}


def definition(name: str) -> str:
    return json.dumps({
        "name": name, "description": "Summarize every markdown file in a folder into digest.md",
        "params": {"folder": {"type": "string", "required": True}},
        "steps": [
            {"id": "files", "tool": "fs_glob", "args": {"pattern": "[a-c]*.md", "root": "{{folder}}"}},
            {"id": "summaries", "needs": ["files"],
             "fan_out": {"over": "{{files.result.files}}", "max_parallel": 2,
                         "agent": {"role": "researcher", "task":
                                   "Read the file {{item.path}} with read_local_file and reply with ONE sentence summarizing it. No other text."}}},
            {"id": "digest", "tool": "write_local_file", "approval": "required", "needs": ["summaries"],
             "args": {"path": "{{folder}}/digest.md", "mode": "overwrite",
                      "content": "# Digest\n\n{{summaries.result.0}}\n\n{{summaries.result.1}}\n\n{{summaries.result.2}}\n\n{{summaries.result.3}}\n"}},
        ],
        "output": "{{digest.result}}"})


def wait_run(g: Grain, rid: str, want: set[str], timeout: float, approve_step: bool = False, log: list | None = None) -> dict:
    end, run = time.time() + timeout, {}
    answered: set[str] = set()
    while time.time() < end:
        run = g.get(f"/workflow-runs/{rid}")
        if run["status"] in want:
            return run
        if approve_step:
            for card in g.pending():
                key = str(card.get("call_id") or card.get("id"))
                if key in answered or rid not in key:
                    continue
                g.post(f"/approvals/{key}", {"decision": "allow"})
                answered.add(key)
                if log is not None:
                    log.append(key)
        time.sleep(0.5)
    return run


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("port", type=int)
    ap.add_argument("scratch", nargs="?")
    ap.add_argument("--kill-cmd")
    ap.add_argument("--start-cmd")
    a = ap.parse_args()
    g = Grain(a.port)
    scratch = Path(a.scratch or tempfile.mkdtemp(prefix="wf-e2e-")).resolve()
    folder = scratch / "notes"
    folder.mkdir(parents=True, exist_ok=True)
    for n, t in FILES.items():
        (folder / n).write_text(t)
    s = g.get("/settings")
    g.put("/settings", {**s, "workspaceRoots": [str(folder)]})
    check("workspaceRoots set to the scratch folder", g.get("/settings")["workspaceRoots"] == [str(folder)])

    # ---- save + validate
    name = f"digest-{int(time.time()) % 100000}"
    v = g.post("/workflows/validate", {"text": definition(name)})
    check("definition validates", v["ok"], str(v["errors"]))
    wf = g.post("/workflows", {"text": definition(name)})
    check("workflow saved", bool(wf.get("id")), wf.get("id", ""))

    # ---- full run
    run = g.post(f"/workflows/{wf['id']}/runs", {"params": {"folder": str(folder)}})
    check("run proposed, awaiting approval", run["status"] == "awaiting_approval", f"{run['id']} {run['plan_digest'][:12]}")
    try:
        g.post(f"/workflow-runs/{run['id']}/approve", {"plan_digest": "0" * 64})
        check("wrong digest refused", False)
    except Exception as e:  # noqa: BLE001
        check("wrong digest refused", "409" in str(e))
    g.post(f"/workflow-runs/{run['id']}/approve", {"plan_digest": run["plan_digest"]})
    answered: list = []
    fin = wait_run(g, run["id"], {"done", "failed", "cancelled", "interrupted"}, 420, approve_step=True, log=answered)
    steps = {x["step_id"]: x for x in fin["steps"]}
    check("run done", fin["status"] == "done", f"{fin['status']} {fin.get('error')}")
    check("step approval was asked for the digest write", any(":digest" in k for k in answered), str(answered))
    check("every step done", all(x["status"] == "done" for x in steps.values()), str({k: v["status"] for k, v in steps.items()}))
    check("fan-out produced 4 summaries", len((steps.get("summaries") or {}).get("result") or []) == 4)
    dig = folder / "digest.md"
    body = dig.read_text() if dig.exists() else ""
    check("digest.md written", dig.exists() and len(body) > 40, body[:200].replace("\n", " | "))
    check("digest mentions the files' topics",
          sum(k in body.lower() for k in ("photo", "recipe", "sourdough", "hik", "rainier", "debian", "server")) >= 3)

    # ---- interruption + resume
    if a.kill_cmd and a.start_cmd:
        if dig.exists():
            dig.unlink()
        run2 = g.post(f"/workflows/{wf['id']}/runs", {"params": {"folder": str(folder)}})
        g.post(f"/workflow-runs/{run2['id']}/approve", {"plan_digest": run2["plan_digest"]})
        t0, r = time.time(), {}
        while time.time() < t0 + 120:
            r = g.get(f"/workflow-runs/{run2['id']}")
            st = {x["step_id"]: x for x in r["steps"]}
            if st["files"]["status"] == "done" and st["summaries"]["status"] == "running" and 1 <= len(st["summaries"].get("items") or {}) < 4:
                break
            time.sleep(0.1)
        files_end = {x["step_id"]: x for x in r["steps"]}["files"].get("ended_at")
        kept = dict({x["step_id"]: x for x in r["steps"]}["summaries"].get("items") or {})
        subprocess.run(a.kill_cmd, shell=True, check=False)
        time.sleep(1)
        subprocess.run(a.start_cmd, shell=True, check=False)
        g = Grain(a.port)
        r = g.get(f"/workflow-runs/{run2['id']}")
        check("after restart the run is interrupted", r["status"] == "interrupted", r["status"])
        g.post(f"/workflow-runs/{run2['id']}/resume")
        fin2 = wait_run(g, run2["id"], {"done", "failed", "cancelled"}, 420, approve_step=True)
        st2 = {x["step_id"]: x for x in fin2["steps"]}
        check("resumed run done", fin2["status"] == "done", f"{fin2['status']} {fin2.get('error')}")
        check("done step not re-run (files ended_at unchanged)", st2["files"].get("ended_at") == files_end, str(files_end))
        final = st2["summaries"].get("result") or []
        check("fan-out items finished before the kill were kept, not re-run",
              bool(kept) and all(final[int(i)] == v for i, v in kept.items()), f"kept {sorted(kept)}")
        check("digest.md rewritten after resume", dig.exists())

    # ---- command with subtask
    cname = f"describe-{int(time.time()) % 100000}"
    cmd = g.post("/commands", {"text": f"---\nname: {cname}\ndescription: one-line description of a file\nsubtask: true\nrole: researcher\n---\n"
                                       "Read the file $1 with read_local_file and reply with exactly one sentence describing it."})
    check("command saved with subtask", cmd["subtask"] is True, cmd["id"])
    cid = g.new_chat("cmd e2e")
    out = g.chat(cid, f"Run my saved command {cname} with the argument {folder / 'bravo.md'} using the command_run tool and tell me its result.",
                 approve=lambda c: "allow", timeout=300)
    names = [t.get("name") or t.get("tool") for t in out["tools"]]
    check("command_run called", "command_run" in names, str(names))
    check("subtask answer mentions the recipes", any(k in out["text"].lower() for k in ("recipe", "sourdough", "miso", "tart")), out["text"][:200])

    bad = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(bad)}/{len(CHECKS)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
