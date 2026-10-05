import type { Conversation } from '@shared/types'
import { filterCommands } from '../features/notes/slash'

/**
 * @mentions in the chat composer: `@` opens the same caret menu `/` does, listing agents. Pure helpers, no UI.
 */
export interface MentionAgent { name: string; description: string }
export interface MentionItem { key: string; label: string; hint: string; /** The whole draft after picking this row. */ insert: string }

const NAME = /^[a-z0-9][a-z0-9_-]{0,39}$/

/** An `@` at the start of the draft or after whitespace, with the name typed since. Null when the caret is not in one. */
export function detectMention(value: string, caret: number): { start: number; query: string } | null {
  let i = caret
  while (i > 0 && value[i - 1] !== '@') {
    if (!/[a-z0-9_-]/i.test(value[i - 1])) return null
    i--
  }
  if (i === 0) return null
  const start = i - 1
  if (start > 0 && !/\s/.test(value[start - 1])) return null // "mail@example.com" is an address
  const query = value.slice(start + 1, caret)
  return query.length > 40 ? null : { start, query }
}

/** The rows of the `@` menu for the draft (at most `max`), or null when it should be closed. Picking one writes `@name ` over what was typed. */
export function mentionItems(text: string, caret: number, agents: MentionAgent[], max = 8): MentionItem[] | null {
  const hit = detectMention(text, caret)
  if (!hit) return null
  const rows = filterCommands(agents.map((a) => ({ a, label: a.name, keywords: a.description.split(/\s+/).filter(Boolean) })), hit.query).slice(0, max)
  const rest = text.slice(caret).replace(/^ /, '')
  return rows.length
    ? rows.map(({ a }) => ({ key: `agent:${a.name}`, label: `@${a.name}`, hint: a.description.slice(0, 48), insert: `${text.slice(0, hit.start)}@${a.name} ${rest}` }))
    : null
}

/** A draft that opens with `@name` for a known agent: who it is for and the message without the mention. Null otherwise. */
export function routeMention(text: string, names: string[]): { agent: string; text: string } | null {
  const m = /^\s*@([a-z0-9][a-z0-9_-]*)(?:\s+([\s\S]*))?$/.exec(text)
  if (!m || !NAME.test(m[1]) || !names.includes(m[1])) return null
  const rest = (m[2] ?? '').trim()
  return rest ? { agent: m[1], text: rest } : null
}

/** The agent's most recently touched chat that is still open, or null (the caller starts a new one). */
export function latestAgentChat(conversations: Conversation[], agent: string): string | null {
  let best: Conversation | null = null
  for (const c of conversations) {
    if (c.settings.agent !== agent || c.archived_at) continue
    if (!best || c.updated_at > best.updated_at) best = c
  }
  return best?.id ?? null
}

export type AgentState = 'needs-you' | 'working' | 'idle'

/** The status an agent's header and Library row show, from the backend's counts (GET /agents/status). Needs-you wins. */
export function agentState(s: { working: number; needs_you: number } | undefined): { state: AgentState; label: string } {
  if (s && s.needs_you > 0) return { state: 'needs-you', label: `Needs you (${s.needs_you})` }
  if (s && s.working > 0) return { state: 'working', label: 'Working' }
  return { state: 'idle', label: 'Idle' }
}
