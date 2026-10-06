import type { Message } from '../../../shared/types'

export interface RoutineDraft { name: string; prompt: string }

const oneLine = (s: string, n: number): string => {
  const t = s.replace(/\s+/g, ' ').trim()
  return t.length > n ? `${t.slice(0, n - 1)}…` : t
}

/**
 * The job editor's starting point for "Schedule as routine": the user turn that led to the reply at `idx`, plus one
 * line on what the reply did (the tools it used, else how it began). Null when `idx` is not an assistant reply
 * with a user turn before it.
 */
export function routineDraftFrom(messages: Message[], idx: number): RoutineDraft | null {
  const reply = messages[idx]
  if (!reply || reply.role !== 'assistant') return null
  let ask: Message | undefined
  for (let i = idx - 1; i >= 0 && !ask; i--) if (messages[i].role === 'user' && messages[i].content.trim()) ask = messages[i]
  if (!ask) return null
  const tools = [...new Set((reply.tool_events ?? []).map((t) => t.name))]
  const did = tools.length ? `It used ${tools.slice(0, 6).join(', ')}.` : oneLine(reply.content, 160)
  const prompt = `${ask.content.trim()}${did ? `\n\nLast time, the reply did this: ${did}` : ''}`
  return { name: oneLine(ask.content, 60), prompt }
}
