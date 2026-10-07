# Agents

An agent is a saved role: a prompt, an optional model and step limit, the tools it
may use, the approved skills it carries, and a face. Library → Agents creates them
(describe one and a model drafts it), and an agent is inert until you approve it.
Editing its prompt withdraws the approval. A reply reaches a Library agent with
`delegate agent=<name>`, which runs a background worker as that agent.

## Scope

Beside the prompt an agent has scope fields. The editor shows them; the agent's page
edits some of them without withdrawing the approval, because none of them is model
written.

| Field | What it does |
| --- | --- |
| label | One line naming the job ("Inbox triage"). Shown on its row and page. |
| boundaries | What it must ask before doing, and what it never does. Fenced into its system prompt as a "Boundaries" block, after the prompt and skills. |
| notes | "What this agent should remember", edited on the Memory tab. Fenced into the prompt after the boundaries. |
| workspace | A folder the agent works in when its chat has none bound. Same guard as a chat's working folder. |
| tool settings | Tool modes for this agent only. Resolution order is chat, then agent, then project, then global. A tool that always asks (mail send, calendar delete, file trash) stays at ask whatever the agent says. |

## Agent page

Click an agent in Library → Agents.

- **Chats**: conversations bound to it, and "New chat as <name>".
- **Routines**: its jobs, with the enabled switch, next run, run history and Run now.
  "New routine…" creates a job bound to the agent. A routine runs as the agent
  (its prompt, skills, boundaries, notes and tool settings) and is an ordinary job
  run: proposal-only. A routine whose agent was deleted stops
  with an error instead of running without its limits. A task scheduled with
  `schedule_task` from the agent's own chat joins its routines.
- **Skills**: which approved skills it carries.
- **Memory**: the notes field.
- **Activity**: its last 20 runs across its chats and routines.

The header shows idle, working or needs you, counted from its chats and routines
(running replies, pending approval cards and pending proposals). The same mark sits
on its row in the list.

## @mentions

Typing `@` in the composer lists agents, the same way `/` lists commands. A message
that starts with `@name` goes to that agent's most recent open chat (a new one if it
has none) and the window switches to it. An `@name` elsewhere in a message stays in
the current chat; the model sees a hint under the turn to hand the task to that agent.
In a chat that delegates to workers (every ordinary chat, autonomous or not) the hint
says `delegate agent=<name>`, which runs a background worker as that agent; desks that
plan first, workflows and crews still use `agent_spawn`.
