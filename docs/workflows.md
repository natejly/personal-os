# Workflows

A workflow is a repeatable multi-step job written down once as data (JSON, or YAML when PyYAML is
installed), approved by hash, and resumable. Definitions and runs live in Library → Automations; the
engine is `backend/personal_os/workflows.py`.

## A definition

```json
{
  "name": "folder-digest",
  "description": "Summarize every file in a folder into digest.md",
  "params": { "folder": { "type": "string", "required": true } },
  "steps": [
    { "id": "scan", "tool": "read_local_file", "args": { "path": "{{folder}}" } },
    { "id": "summaries", "fan_out": { "over": "{{scan.result.entries}}", "max_parallel": 3,
      "agent": { "role": "researcher", "task": "Read {{folder}}/{{item}} and summarize it." } } },
    { "id": "write", "tool": "write_local_file", "approval": "required",
      "args": { "path": "{{folder}}/digest.md", "content": "{{summaries.result}}" } }
  ],
  "output": "{{write.result}}"
}
```

A step is exactly one of `tool`, `agent` or `fan_out`. Templates are `{{param}}`, `{{step.result}}`
(with `.key` / `.0` paths into it) and, inside a fan-out's agent, `{{item}}` and `{{index}}`; they are
substituted, never evaluated. `needs` lists the steps a step waits for; a reference to a step's result
is a dependency too. `when` skips a step whose template renders falsy. Tools that would start more
work on their own (nested workflows, desks, scheduling, asking the user) cannot be steps.

## Running

- **Nothing starts by itself.** Proposing a run (from the Library, from chat with `workflow_run`, or
  from a scheduled job) records it with its expanded plan and a `plan_digest`. The run starts only when
  the user approves exactly that digest. Editing the definition afterwards makes every waiting run
  stale.
- **Waves.** Every step whose dependencies are settled starts at once, so two agents with nothing
  between them work side by side. A failed step blocks only the steps that need it; the rest finish.
- **Same permissions as chat.** Tool steps go through the toolbox: the tool's mode, taint forcing, the
  argument-pattern rules and the idempotency journal all apply. A step marked `approval: required`
  parks on a durable approval card first; two parked steps in one wave each keep the run waiting until
  both are answered. Agent and fan-out steps spawn subagents under the usual caps, and their text taints
  the run, so an external tool later in it asks.
- **Resume.** A done step never runs again. Resume starts at the first step that is not done; a
  side-effecting call that began before a crash is not repeated. Fan-out items already finished are
  kept.

## What a run records

Each run is a `workflow_runs` row plus one `workflow_steps` row per step (status, result, fan-out
`items`, error, the approval card id) and a run tape in `agent_runs`, so approvals and subagents hang
off a real run. An agent or fan-out step also records the subagent runs it spawned in `agents`, which
is what lets a run be drawn as a tree. Every run or step write is announced on the app event stream as
`workflow_run` (payloads stripped), and `GET /crew/{id}` returns the tree for a run, a saved workflow
(its latest run) or a desk: the root, its steps, and every subagent under it with the parent it hangs
off, its task, and while it runs the tool call in flight. Spaces draws that as the crew window (see
`docs/spaces.md`).
