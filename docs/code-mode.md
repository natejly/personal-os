# Code mode

Code mode lets the assistant write one short Python script that calls many app tools inside the sandbox, filters and
combines what they return, and hands back only what it prints and a final value. The conversation sees the answer, not
every intermediate result.

It is the `tools` argument of `run_python`:

```
run_python(code, timeout=60, tools=["fs_grep", "read_local_file"])   # a list of names
run_python(code, tools="offered")                                    # every bridgeable tool offered to this chat
```

Without `tools` the script is a plain sandboxed run with no access to app tools.

## Permissions

Every inner call goes through `Bridge.handle`, the same gate a direct call uses. There is no side door:

- Tool modes for this chat apply: `off` refuses, `ask` shows a card, `on` runs.
- `Toolbox.precheck` refuses bad arguments, and a repo edit that belongs to a coding agent, before any card.
- Taint upgrades `on` to `ask` after untrusted content, and permission rules apply (deny beats ask beats allow).
- In Auto mode a call the reply loop would send to the reviewer is reviewed here too, because the script may have
  computed its arguments from content the reviewer never saw.
- Guards, the approval card (`bridge_approve`) and the journal (`bridge_call`) behave as for a direct call.
- A tool that taints (web search, fetching a page, reading mail) taints the run when the script calls it, so later calls
  in the same script and later turns see the upgraded modes.

An `ask` call opens a normal approval card and parks the script. The script's clock stops while the card is open, so
time spent waiting for you never counts against the timeout. If you decline, the call raises `ToolError` in the script
and the script can catch it or stop. With nobody to ask (a background run), the call is refused rather than run.

Bridgeable tools are any tool the chat may use whose mode is `on` or `ask`, except:

- tools that always show a card (the `alwaysAsk` set),
- `agent_spawn`, `delegate`, workflow tools and scheduling tools,
- `run_python` itself.

Extension tools and MCP tools are bridgeable. A name outside this set, or one that is off for the chat, is reported back
in `tools_refused` with the reason.

## Limits

| Limit | Value |
| --- | --- |
| `codeMode` setting | On by default. Off removes the `tools` argument. |
| `codeModeMaxCalls` setting | Inner calls per run, default 100, range 1 to 500. |
| Timeout | Up to 300 s for a bridged run, approval waits excluded. |
| Stdout | 50,000 characters shown: 40% from the start, 60% from the end, the middle counted. |
| Full stdout | Stored; the result carries a `result_id` for `read_tool_result` with an offset. |
| One call's result | About 2 MB; larger results are refused for that call. |
| Concurrency | `gather` runs at most `parallelReads` calls at once, and only parallel-safe (read-only) tools run concurrently. The rest run one at a time. |

## The client API

Inside the script, `grain_tools` is already importable:

```python
import grain_tools
from grain_tools import ToolError

# Typed stubs, one per offered tool, with docstrings from the tool schema
hits = grain_tools.fs_grep(pattern="TODO", root="src")

# The same call by name
hits = grain_tools.call("fs_grep", pattern="TODO", root="src")

# Look up what a tool takes and returns
print(grain_tools.describe("fs_grep"))

# Find a tool by keyword among those offered
print(grain_tools.search("calendar"))
```

Errors: a refusal (not offered, off, declined, call limit reached) raises `ToolError`. A tool's own failure comes back as
data in the result, so check it where it matters.

```python
try:
    grain_tools.fs_edit(path="a.py", old="x", new="y")
except ToolError as e:
    print("skipped:", e)
```

Fan-out with `gather`. Build the calls with `call_later`, which records a call without running it:

```python
paths = [h["path"] for h in grain_tools.fs_glob(pattern="docs/*.md")["files"]]
pages = grain_tools.gather(*[grain_tools.call_later("read_local_file", path=p) for p in paths])
big = [p for p, r in zip(paths, pages) if len(str(r)) > 20_000]
print(big)
```

A worked chain: find, read, filter, report.

```python
files = grain_tools.fs_grep(pattern="deprecated", root="backend")["matches"]
by_file = {}
for m in files:
    by_file.setdefault(m["path"], []).append(m["line"])
print({p: len(v) for p, v in sorted(by_file.items(), key=lambda kv: -len(kv[1]))[:10]})
```

The last expression is returned as the final value.

## What comes back

- `stdout`, shaped as above.
- The final value of the script.
- `calls`: a compact trace, one entry per inner call: `{name, ok, ms, approved?}`. `approved` appears when a card was
  answered. The tool card renders it; payloads are not included.
- A summary line, for example the number of calls made and how many were refused.
- `result_id` and a note when stdout was cut.

A script that raises returns the end of the traceback plus the calls that ran, so the assistant can see which side
effects already happened before it retries.

## Platform caveat

On macOS the script runs under a Seatbelt profile. Its only network path is the bridge's Unix socket, so it cannot reach
the internet or any other service; every outside effect has to go through a gated tool.

Linux has no OS sandbox for `run_python`. Every run there asks for approval, bridged or not, and the card shows the code.
The bridge gating above still applies to each inner call on top of that.

## Token savings: when it helps

Vendor-reported figures, unverified in Grain: published write-ups of this technique report roughly 37% fewer tokens on
multi-step research tasks, a few points better accuracy, and far larger reductions (above 90%) on single hand-picked
examples with very large intermediate data. Treat these as an upper bound on what to expect; measure on your own work.

It helps when:

- three or more calls depend on each other,
- results are large and only a slice matters (grep output, long documents, many rows),
- the work is a loop or a conditional (every file, every event this week),
- many independent reads can fan out in parallel.

It does not help, and costs an extra step, when:

- one tool call does the job,
- the output is small,
- the model needs to look at each intermediate result to decide what to do next,
- the step is a write the user should review one at a time (each still asks, but a card inside a script is easy to
  lose in the flow).

Rule of thumb: reach for direct calls first and a script when the next call would only be a mechanical consequence of
the last result.
