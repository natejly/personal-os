import type { Message, SubagentView, ToolEvent } from '@shared/types'

/** One row of a worker's chat: a Message the main chat's MessageView can draw, and who sent a user-role row. */
export type WorkerRow = { message: Message; from?: 'user' | 'agent' }

const parseArgs = (s: string): Record<string, unknown> => {
  try { const v = JSON.parse(s); return v && typeof v === 'object' ? v : {} } catch { return {} }
}
const text = (c: unknown): string =>
  typeof c === 'string' ? c : Array.isArray(c) ? c.map((p) => (typeof p === 'string' ? p : typeof p?.text === 'string' ? p.text : '')).join('') : ''

/**
 * A worker's model history as chat rows. Each user-role row stands alone (the main agent's task and steers, or the
 * user's own messages); the assistant and tool rows after it fold into one reply, its tool calls as tool events with
 * their results, the way a reply in the main chat carries them. A call with no result yet is pending while `live`.
 */
export function workerRows(messages: SubagentView['messages'], conversationId: string, live: boolean): WorkerRow[] {
  const rows: WorkerRow[] = []
  const results = new Map(messages.filter((m) => m.role === 'tool' && m.tool_call_id).map((m) => [m.tool_call_id!, text(m.content)]))
  let reply: Message | null = null
  messages.forEach((m, i) => {
    const base = { id: `w${i}`, conversation_id: conversationId, model: null, error: null, context_used: null, trace: null, created_at: 0 }
    if (m.role === 'user') {
      reply = null
      rows.push({ message: { ...base, role: 'user', content: text(m.content), tool_events: null }, from: m.from === 'user' ? 'user' : 'agent' })
    } else if (m.role === 'assistant') {
      if (!reply) {
        reply = { ...base, role: 'assistant', content: '', tool_events: [] }
        rows.push({ message: reply })
      }
      const said = text(m.content).trim()
      if (said) reply.content = reply.content ? `${reply.content}\n\n${said}` : said
      for (const c of m.tool_calls ?? []) {
        const id = c.id ?? `${reply.id}-${reply.tool_events!.length}`
        const done = results.has(id)
        const ev: ToolEvent = { id, name: c.function.name, arguments: parseArgs(c.function.arguments), result_preview: results.get(id) ?? '', duration_ms: 0, error: null, pending: !done && live }
        reply.tool_events!.push(ev)
      }
    }
  })
  return rows
}
