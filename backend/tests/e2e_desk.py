"""Live e2e for a cowork desk deliverable. Usage: python backend/tests/e2e_desk.py <port> [token]

Needs a running backend with real models (see the e2e backend helper). Runs two desks:
 1. plan mode: approve the plan card, wait for review with an output, check the file is a real
    comparison, accept it into a doc and verify the doc holds the same bytes.
 2. 'ask' mode: every change is carded; allow them, expect review with an output again.
Exit code 0 only if every check passes.
"""
from __future__ import annotations

import sys
import time

import httpx

BRIEF = ("Research three note-taking methods (Zettelkasten, PARA, Cornell) from your own knowledge and write a "
         "one-page comparison to outputs/report.md, then nominate it with desk_deliver and finish.")
FAILS: list[str] = []


def check(name: str, ok: bool, evidence: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  [{evidence[:300]}]" if evidence else ""))
    if not ok:
        FAILS.append(name)


class C:
    def __init__(self, port: int, token: str | None):
        self.c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60,
                              headers={"Authorization": f"Bearer {token or f'e2e-{port}'}"})

    def req(self, m: str, p: str, body=None, **kw):
        r = self.c.request(m, p, json=body, **kw)
        r.raise_for_status()
        return r.json() if r.content else None


def drive(c: C, desk_id: str, conv_id: str, timeout: float = 600) -> dict:
    """Answer every approval card of the desk's conversation with allow until it parks for review."""
    seen: dict[str, str] = {}
    end = time.time() + timeout
    d: dict = {}
    while time.time() < end:
        d = c.req("GET", f"/cowork/desks/{desk_id}")
        rows = c.req("GET", "/approvals")
        rows = rows.get("approvals", rows) if isinstance(rows, dict) else rows
        for a in rows:
            if a.get("conversation_id") != conv_id:
                continue
            key = str(a.get("call_id") or a.get("id"))
            if key in seen:
                continue
            seen[key] = str(a.get("tool") or a.get("name") or a.get("kind"))
            print("  card:", key, seen[key])
            c.req("POST", f"/approvals/{key}", {"decision": "allow"})
        if d["status"] in ("review", "done", "failed", "stopped"):
            break
        if d.get("question"):
            print("  question:", d["question"][:200])
            c.req("POST", f"/cowork/desks/{desk_id}/message", {"content": "Use your best judgement and proceed."})
        elif d["status"] in ("interrupted", "paused"):
            try:
                c.req("POST", f"/cowork/desks/{desk_id}/resume", {})
            except httpx.HTTPStatusError:
                pass
        time.sleep(2)
    d["_cards"] = seen
    return d


def main() -> int:
    port = int(sys.argv[1])
    c = C(port, sys.argv[2] if len(sys.argv) > 2 else None)
    for mode in ("plan", "ask"):
        print(f"== autonomy={mode}")
        out = c.req("POST", "/cowork/desks", {"brief": BRIEF, "autonomy": mode, "title": f"e2e {mode}"})
        did, cid = out["desk"]["id"], out["conversation_id"]
        check(f"{mode}: desk created and started", bool(out.get("run_id")), f"desk={did} run={out.get('run_id')}")
        d = drive(c, did, cid)
        cards = d.get("_cards", {})
        print("  status:", d["status"], d.get("status_reason"), "cards:", cards)
        check(f"{mode}: desk reached review", d["status"] == "review", f"{d['status']} {d.get('status_reason')}")
        if mode == "plan":
            check("plan: a plan was proposed", d.get("plan") is not None, f"plan={(d.get('plan') or {}).get('status')} cards={cards}")
        outs = c.req("GET", f"/cowork/desks/{did}/outputs")
        check(f"{mode}: an output was nominated", bool(outs), str([(o['path'], o['status']) for o in outs]))
        if not outs:
            continue
        o = outs[0]
        txt = c.req("GET", f"/cowork/desks/{did}/file", params={"path": o["path"], "length": 100000})["text"]
        low = txt.lower()
        check(f"{mode}: report names all three methods", all(k in low for k in ("zettelkasten", "para", "cornell")), f"{len(txt)} chars")
        check(f"{mode}: report is a real comparison", len(txt) > 1000 and ("|" in txt or low.count("\n#") >= 3), txt[:200].replace("\n", " "))
        res = c.req("POST", f"/cowork/desks/{did}/accept",
                    {"outputs": [{"output_id": o["id"], "destination": "doc", "title": "Note-taking comparison"}]})["results"][0]
        check(f"{mode}: accept verified", res.get("verified") is True, str(res))
        if res.get("ref"):
            doc = c.req("GET", f"/docs/{res['ref']}")
            check(f"{mode}: doc holds the file's bytes", doc["content"] == txt, f"doc {res['ref']} {len(doc['content'])} chars")
        d2 = c.req("GET", f"/cowork/desks/{did}")
        check(f"{mode}: desk closed as done after accept", d2["status"] == "done", d2["status"])
    print("FAILED:" if FAILS else "ALL PASSED", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
