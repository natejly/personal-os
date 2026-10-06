import type { AgentDef } from '@shared/types'

export const COMMAND_SKELETON = `---
name: my-command
description: One line on what this command does
subtask: false
---
Use $ARGUMENTS as the topic, or $1 for the first word only.
`

/** Rebuild editable markdown from a stored agent row (the backend keeps the prompt in `body`). */
export function agentText(d: Pick<AgentDef, 'name' | 'description' | 'model' | 'steps' | 'tools' | 'skills' | 'hue' | 'hidden' | 'body'>): string {
  return `---\nname: ${d.name}\ndescription: ${d.description}\n` +
    (d.model ? `model: ${d.model}\n` : '') + (d.steps ? `steps: ${d.steps}\n` : '') + (d.hue != null ? `hue: ${d.hue}\n` : '') +
    (d.tools.length ? `tools: ${d.tools.join(', ')}\n` : '') + (d.skills.length ? `skills: ${d.skills.join(', ')}\n` : '') +
    (d.hidden ? 'hidden: true\n' : '') + `---\n${d.body}\n`
}
