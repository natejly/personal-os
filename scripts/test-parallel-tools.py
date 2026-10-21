"""Helpers for test-parallel.sh: shard the backend test files, and merge shard results into one summary.

  shard   N CACHE OUT_PREFIX     write OUT_PREFIX1..N (one test path per line), balanced by cached duration
  times   CACHE JUNIT...         fold the per-file durations in the shards' junit files into CACHE
  backend LOGDIR N               combined backend summary; exit 1 when any shard failed
  e2e     REPORT...              combined e2e summary from Playwright JSON reports; prints failing spec ids to stdout
                                 as "file:line<TAB>title" with --list-failed FILE
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
COUNT = re.compile(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|subtests passed|subtests failed)")


def load(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def shard(n: int, cache: str, prefix: str) -> None:
    files = sorted((BACKEND / "tests").glob("test_*.py"))
    times = load(cache)
    known = [(f, times[f.stem]) for f in files if f.stem in times]
    # Files without a past duration are estimated from their size, at the rate the known files show (or by size alone).
    size_known = sum(f.stat().st_size for f, _ in known)
    rate = sum(t for _, t in known) / size_known if size_known else 1.0
    cost = {f: times.get(f.stem, f.stat().st_size * rate) for f in files}
    loads = [0.0] * n
    buckets: list[list[Path]] = [[] for _ in range(n)]
    if known:  # longest first onto the lightest shard
        for f in sorted(files, key=lambda f: -cost[f]):
            i = loads.index(min(loads))
            buckets[i].append(f)
            loads[i] += cost[f]
    else:  # no history: round-robin down the files sorted by size
        for k, f in enumerate(sorted(files, key=lambda f: -f.stat().st_size)):
            buckets[k % n].append(f)
    for i, b in enumerate(buckets, 1):
        Path(f"{prefix}{i}").write_text("".join(f"tests/{f.name}\n" for f in sorted(b)))
    mode = "past durations" if known else "file size"
    print(f"{len(files)} files in {n} shards by {mode} ({len(files) - len(known)} without history)")


def times(cache: str, junits: list[str]) -> None:
    data = load(cache)
    for j in junits:
        try:
            root = ET.parse(j).getroot()
        except (OSError, ET.ParseError):
            continue
        for tc in root.iter("testcase"):
            if tc.get("name") and tc.get("time"):
                data[tc.get("name")] = round(float(tc.get("time")), 2)
    Path(cache).parent.mkdir(parents=True, exist_ok=True)
    Path(cache).write_text(json.dumps(dict(sorted(data.items())), indent=0) + "\n")


def backend(logdir: str, n: int) -> int:
    totals: dict[str, int] = {}
    failing: list[str] = []
    bad = 0
    for i in range(1, n + 1):
        log = Path(logdir) / f"backend-shard-{i}.log"
        code_file = Path(logdir) / f"backend-shard-{i}.code"
        code = int(code_file.read_text().strip() or 1) if code_file.exists() else 1
        text = log.read_text(errors="replace") if log.exists() else ""
        m = re.search(r"inner totals: (.*?) =*\s*$", text, re.M)
        if m:
            for c, kind in COUNT.findall(m.group(1)):
                totals[kind] = totals.get(kind, 0) + int(c)
        names: list[str] = []
        try:
            for tc in ET.parse(Path(logdir) / f"backend-shard-{i}.xml").getroot().iter("testcase"):
                if tc.find("failure") is not None or tc.find("error") is not None:
                    names.append(f"tests/{tc.get('name')}.py")
        except (OSError, ET.ParseError):
            pass
        if code != 0:
            bad += 1
            if not names:
                names = [f"(shard {i} exited {code} without a junit report)"]
        failing += [f"  {nm}   {log}" for nm in names]
    parts = ", ".join(f"{totals[k]} {k}" for k in ("passed", "failed", "error", "errors", "skipped", "xfailed", "xpassed") if totals.get(k))
    print(f"backend: {parts or 'no totals'} across {n} shards")
    if failing:
        print("backend failing files:")
        print("\n".join(failing))
    return 1 if bad else 0


def walk(suite: dict, path: list[str], out: list[dict]) -> None:
    title = suite.get("title", "")
    here = path + ([title] if title and not title.endswith((".mjs", ".js")) else [])
    for spec in suite.get("specs", []):
        tests = spec.get("tests", [])
        status = [t.get("status") for t in tests]
        raced = "backend exited early" in json.dumps(tests)
        out.append({"raced": raced, "file": spec.get("file", suite.get("file", "")), "line": spec.get("line", 0),
                    "title": " > ".join(here + [spec.get("title", "")]),
                    "status": "flaky" if "flaky" in status else "failed" if "unexpected" in status
                    else "skipped" if status and all(s == "skipped" for s in status) else "passed" if spec.get("ok") else "failed"})
    for sub in suite.get("suites", []):
        walk(sub, here, out)


def specs(reports: list[str]) -> list[dict]:
    out: list[dict] = []
    for r in reports:
        for s in load(r).get("suites", []):
            walk(s, [], out)
    return out


def e2e(reports: list[str], list_failed: str | None) -> None:
    allspecs = specs(reports)
    count = {k: sum(1 for s in allspecs if s["status"] == k) for k in ("passed", "failed", "skipped", "flaky")}
    print(f"e2e: {count['passed']} passed, {count['failed']} failed, {count['skipped']} skipped, {count['flaky']} flaky")
    failed = [s for s in allspecs if s["status"] == "failed"]
    # A failure that is only the harness's own "backend exited early" is left to the serial rerun line below it.
    for s in failed:
        if not (list_failed and s["raced"]):
            print(f"  FAILED {s['file']}:{s['line']}  {s['title']}")
    if list_failed:
        Path(list_failed).write_text("".join(f"{s['file']}:{s['line']}\t{s['title']}\n" for s in failed))


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "shard":
        shard(int(args[0]), args[1], args[2])
    elif cmd == "times":
        times(args[0], args[1:])
    elif cmd == "backend":
        sys.exit(backend(args[0], int(args[1])))
    elif cmd == "e2e":
        lf = None
        if args and args[0] == "--list-failed":
            lf, args = args[1], args[2:]
        e2e(args, lf)
    elif cmd == "rerun":  # rerun REPORT FAILED_LIST: which of the failed specs passed on the serial rerun
        done = {f"{s['file']}:{s['line']}": s["status"] for s in specs([args[0]])}
        for line in Path(args[1]).read_text().splitlines():
            key, title = line.split("\t", 1)
            st = done.get(key)
            label = "load-flaky" if st in ("passed", "flaky") else "skipped on rerun" if st == "skipped" else "STILL FAILING"
            print(f"  {label}  {key}  {title}")
            if label == "STILL FAILING":
                print("STILL", file=sys.stderr)
    else:
        sys.exit(f"unknown command {cmd}")
