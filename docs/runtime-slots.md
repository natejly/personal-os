# Runtime slots

Six seams of the app have a built-in provider and room for others: `model.client` (how a model call is sent), `model.router` (which model a turn uses), `tools.source` (extra tools beside the built-in ones and the connectors), `sandbox.backend` (how a confined process is confined) `agent.loop` (how a reply's rounds are run: the chat loop, or "Plan, execute, verify") and `memory.system` (how memory is learned, recalled and searched). An extension offers a provider; only you select one.

## Choosing

Settings > Runtime shows, for each slot, which provider is in effect, where that choice was made and why, and lets you choose for everything, for the focused project or for the focused chat. A chat's own choice wins over its project's, then an enabled extension's default, then the one set everywhere, then the built-in. A reply that used a provider other than the built-in says so in its footer, in its context and in its trace.

Choosing a provider that is looser than the one in effect (a remote model client over a local one, a sandbox that enforces less, a tool source that adds tools) asks first and lists the difference. An extension's `defaults.runtime` is held the same way.

If a chosen provider is turned off or removed, the built-in takes over with a notice, but only when it is no less private (model client) or no less strong (sandbox). Otherwise the call is refused.

## Plan, execute, verify

Choose it for a chat, a project or everywhere and a reply goes in four parts: one model call writes a plan of at most eight steps (shown in the progress panel; listing a step approves nothing), each step is worked with tools for at most six rounds, one call checks the work against the plan, and the final answer is written. If the check finds gaps the loop makes one repair pass and checks once more; if the second check also fails, the answer says what is not verified and does not claim the task is done. A turn is held to `loopModelCalls` model calls (60 unless you change it; see [the agent loop](agent-loop.md)) and ends with an explanation if it uses them. Every tool call still goes through the same permission checks and approval cards as in the chat loop, Stop and steering work the same way, and the cached prompt prefix and the tool list are unchanged, so switching loops between turns costs no cache. Only the final answer is streamed as the reply.

## Memory system

`memory.system` decides what a chat remembers. The built-in provider is the app's own memory: auto-learn after a reply, the standing-preferences block, the recalled-memories and knowledge-graph blocks, and the memory tools. A provider implements `personal_os.runtime.memory_api.MemorySystem`: `learn` (after a settled reply), `recall` (volatile blocks for a turn), `profile` (the stable standing-preferences block), `remember`, `forget`, `search` and `status`. It is chosen for a chat, a project or everywhere like any other slot, and only built-in extensions can provide it for now.

The chat's own switches stay in front of any provider: with memory off nothing is recalled, profiled or learned, with the graph off no graph block is used, and a chat with auto-learn off never reaches `learn`. A provider that raises, takes too long or returns the wrong kind of block is replaced by the built-in memory for that call, and a card in the inbox says so once. The Memory settings page and its routes still read the built-in memory tables.

The built-in History digest extension (`memory-tree/digest`, see [the history digest](memory-tree.md)) is a layering provider: it hands every call to the built-in memory and adds one volatile block, the digest, to what `recall` returns.

## What never changes

Whatever is selected, the permission checks, approvals, the call journal and secret redaction run exactly as before. A model client sees the request, never your settings or keys. A tool source's tools are named `x__<extension>__<tool>`, are always `ask`, and taint the run like a connector's. A sandbox backend that cannot enforce a request is never bypassed: the command is refused, not run unconfined. An agent loop reaches tools only through the same step the chat loop uses (so every card and refusal applies), gets its context from the same contributors, cannot write the cached prefix or change which tools are offered, and cannot decide an approval; a run in which a tool ran around that step ends as failed with `loop_violation`.

## For extension authors

Declare the provider in `extension.json` (`contributes.runtime: [{slot, id, label}]` and the slot's permission, `runtime:model`, `runtime:router`, `runtime:tools`, `runtime:loop`) and supply it from `activate`: `api.provide("model.router", MyRouter(), id="mine", label="Mine")`. A user extension may provide the first three slots, and an agent loop once you grant it `runtime:loop` on its card when you enable it (without the grant the provider is refused); a sandbox backend is built-in only. `grain ext new <id> --loop` writes a working loop provider; see [the agent loop](agent-loop.md). A loop module may import only `personal_os.runtime.loop_api` from the app and no introspection module from the standard library. A model client's `complete` returns `grain_sdk.ModelReply(text)` and takes a `grain_sdk.ModelRequest`. `grain_sdk.testing` has `FakeModelClient`, `FakeRouter`, `FakeToolSource`, `FakeSandbox`, `slot_contract(slot, factory)` to run a slot's contract suite and `Harness.select(slot, provider_id)`. The Test button in Settings > Runtime runs the same suite.
