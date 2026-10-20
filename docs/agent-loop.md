# The agent loop

A reply is a loop: the model is called, it asks for tools, the tools run, and the results go back until the model answers without asking for more. Two loops ship, you choose one per chat, project or everywhere, and an extension can offer a third. Whatever loop runs, every tool call goes through the same permission checks, approval cards and call journal, Stop and steering work the same way, and the cached prompt prefix is not touched.

## The two loops

**Chat loop** (`core/chat-loop`, the default). Rounds of model call, then tools, until the model stops asking for tools. Nothing caps the rounds by default. A reply ends when the model answers, a stuck breaker fires (identical calls repeating, one tool failing again and again, a denial streak, the stuck detector), you press Stop, or the optional `loopMaxRounds` cap is reached. When a breaker or the cap ends tool use, the model is asked once for a closing answer with what it has.

**Plan, execute, verify** (`core/plan-execute-verify`). One model call writes a plan (shown in the progress panel; listing a step approves nothing), each step is worked with tools for at most six rounds, one call checks the work against the plan, and the final answer is written. If the check finds gaps it makes repair passes and checks again; when the last check still fails, the answer says what is not verified and does not claim the task is done. Only the final answer is streamed as the reply. It is held to `loopModelCalls` model calls.

## Tunables

All of these are settings, read when a turn starts (a loop is never swapped or retuned mid-turn). A stored value outside its range is clamped; a missing or invalid one reads as the default.

| Setting | Default | Range | What it changes |
|---|---|---|---|
| `loopMaxRounds` | 0 | 0 to 200 | Tool rounds of the chat loop. 0 means no cap. When the cap is reached the model is asked for a closing answer, the same path as a stuck breaker. |
| `loopModelCalls` | 60 | 5 to 500 | Model calls one turn of an alternative loop (plan, execute, verify, or an extension's) may make. The turn ends with an explanation when they are used. The chat loop is not held to it. |
| `loopRepeatLimit` | 5 | 2 to 20 | Identical tool calls in a row before the reply stops using tools. Applies to every loop. |
| `loopToolErrorLimit` | 3 | 1 to 10 | Consecutive errors from one tool before it is switched off for the rest of the reply and the model is told to change approach. |
| `loopDoomLimit` | 3 | 1 to 10 | How many times the same call may be made before the next identical call asks for approval again, whatever the permission mode. |
| `stuckSensitivity` | `standard` | `off`, `relaxed`, `standard`, `strict` | The detector for loops the repeat limit cannot see (ping-pong between two calls, re-reading something that never changes, the same failure with varied arguments). `standard` is the shipped thresholds, `relaxed` doubles them, `strict` takes one off each (never below 2), `off` disables the detector. |
| `finalRoundSeconds` | 90 | 10 to 600 | How long the closing-answer model call may stay open before it is abandoned. |
| `toolResultInlineChars` | 12000 | 2000 to 60000 | A tool result this long or shorter goes into the context whole; a longer one is stored and the model gets a preview and a handle. |
| `planLoopMaxSteps` | 8 | 2 to 20 | The most steps the plan may have in plan, execute, verify. |
| `planLoopVerifyPasses` | 1 | 0 to 3 | Repair passes after a failed check in plan, execute, verify. 0 checks once and answers with what is not verified. |

Settings that shape the loop but are not specific to it:

| Setting | Default | Range | What it changes |
|---|---|---|---|
| `parallelReads` | 0 (automatic) | 1 to 8 | Read-only calls of one round that run together. |
| `llmRetries` | 3 | 0 to 10 | Retries before a reply's first token. |
| `llmIdleSeconds` | 300 | 10 to 3600 | A model stream silent this long is abandoned with an error. |
| `delegationAfterRounds` | 4 | 1 to 20 | Rounds a chat reply works itself before the rest is routed to a worker (routing, not a cap). |

## Choosing a loop

The loop is the runtime slot `agent.loop`. Its providers are `core/chat-loop`, `core/plan-execute-verify` and `<extension id>/<id>` for each loop an enabled extension provides. Choose in Settings > Runtime, or with the API:

```
PUT /runtime
{"slot": "agent.loop", "scope": "chat", "scope_id": "<chat id>", "value": "core/plan-execute-verify"}
```

`scope` is `global`, `project` or `chat` (the latter two need `scope_id`); `value: null` clears the choice at that scope. A profile can also select a loop with `defaults.runtime`. The narrowest choice wins: chat, then project, then profile, then global, then the built-in chat loop. `GET /runtime/resolve` says which provider is in effect and why. The loop is chosen once, as the turn starts. A reply that used a loop other than the default says so in its footer and context.

If the chosen loop's extension is turned off or removed, the chat loop takes over with a notice.

## Writing a loop

```
grain ext new my-loop --loop
cd my-loop
grain ext check .
grain ext test .
```

The scaffold writes three files: `extension.json`, `extension.py` and `tests/test_extension.py`. It is a small "draft, critique, revise" loop: it drafts with tools, asks the model to critique the draft, then streams a revision as the reply.

The manifest provides the slot and declares the permission:

```json
"contributes": { "runtime": [{ "slot": "agent.loop", "id": "draft-critique-revise", "label": "My Loop: draft, critique, revise" }] },
"permissions": ["runtime:loop"]
```

`extension.py` defines a class with an `id`, `bounds` (the rounds the loop may count per scope, for example `{"draft": 6}`) and `async def run(self, turn, h)`, an async generator of events, and supplies it from `activate` with `api.provide("agent.loop", MyLoop(), id="draft-critique-revise", label="...")`.

**The grant.** A user extension can provide `agent.loop` only if you grant it `runtime:loop`: enabling it asks, naming the permission, and revoking or disabling the extension takes the grant away. Without it the provider is refused. Installing a loop never selects it; only you do.

**What `run` gets.** `turn` is plain data (`conversation_id`, `run_id`, `text`, `attachments`, `origin`, `resumed`, `model`, `effort`, `cfg`). The model is chosen by the router slot; a loop never picks a provider. `cfg` is the settings with secrets removed, and `setting_int(turn.cfg, "planLoopMaxSteps")` from `loop_api` reads a loop tunable clamped into its range. The harness `h` has:

- `h.context.build(extra_tail=())` returns the turn's messages: the context pipeline's blocks, then this run's own messages so far. `extra_tail` text joins the uncached tail.
- `h.model.stream(messages, tools=..., tool_choice="auto")` is one model call; it yields `delta` items and ends with an `end` item holding `text`, `tool_calls` and `finish_reason`. It applies the idle limit, Stop and steering.
- `h.tools.run(calls)` runs the calls the last model call asked for, with every check, card and journal row, and returns the tool messages.
- `h.limits.next_round(scope)` counts a round in a scope named in `bounds` and raises `LimitReached` once the bound is spent; `restart(scope)`, `stopped` and `take_steers()` complete it.
- `h.events.emit(event)` publishes an event as the loop goes, and `h.hooks.emit("loop.plan" | "loop.verify", payload)` fires those two hooks.

A loop yields events as `(name, payload)` pairs and may use only `delta` (reply text), `plan` (steps for the progress panel), `span` (a trace span) and `status` (the line shown while working).

**The fence.** A module that defines a loop may import from the app only `personal_os.runtime.loop_api`, and from the standard library none of `gc`, `inspect`, `sys`, `importlib`, `builtins`, `ctypes` or `__import__`. The source is scanned when the provider is registered and by `grain ext check`, on every file of the extension, so the check holds for what the files say and not only for what they imported.

**What the harness forbids.** A loop cannot:

- run a tool except through `h.tools.run`; a run in which a tool ran around that step ends as failed with `loop_violation`;
- change the cached prefix: it is checked before every model call and a call whose prefix was changed fails;
- change which tools are offered, decide an approval, or read settings secrets, keys or the objects behind the harness;
- emit events other than the four above, or fire hooks other than `loop.plan` and `loop.verify`;
- make more than `loopModelCalls` model calls in a turn, or more rounds in a scope than `bounds` declares;
- be swapped in or out in the middle of a turn.

**Testing.** `grain ext test` runs the extension's tests against a scratch data directory. The generated test uses `grain_sdk.testing.Harness`: `h.select("agent.loop", "my-loop/draft-critique-revise")` selects the loop (the harness grants `runtime:loop`, as you would on the card), then `h.chat("...", model_script=[...])` runs a turn with one scripted reply or tool call per model call and returns the streamed text and the messages of each model call.
