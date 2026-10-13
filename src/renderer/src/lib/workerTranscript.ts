import type { Message, RunTapeEvent, Span, SubagentView, ToolEvent } from '@shared/types'
import { stripNoReply } from './noReply'

/** One row of a worker's chat: a Message the main chat's MessageView can draw, and who sent a user-role row. */
export type WorkerRow = { message: Message; from?: 'user' | 'agent' }

const parseArgs = (s: string): Record<string, unknown> => {
  try { const v = JSON.parse(s); return v && typeof v === 'object' ? v : {} } catch { return {} }
}
const text = (c: unknown): string =>
  typeof c === 'string' ? c : Array.isArray(c) ? c.map((p) => (typeof p === 'string' ? p : typeof p?.text === 'string' ? p.text : '')).join('') : ''

type Timing = { duration_ms: number; wait_ms: number }

/** Each finished call's measured duration and approval wait, by call id, from the worker's tape. */
export function tapeTimings(tape: readonly RunTapeEvent[]): Map<string, Timing> {
  const out = new Map<string, Timing>()
  for (const e of tape) if (e.event === 'tool_result') out.set(String(e.data.id), { duration_ms: Number(e.data.duration_ms) || 0, wait_ms: Number(e.data.wait_ms) || 0 })
  return out
}

/**
 * A worker's steps for the trace waterfall: its model rounds (the tape records each twice, started and ended, so the last
 * wins) and its tool calls, each laid after the round that issued it. A call's start is that round's end; calls of one
 * round run side by side as far as the tape knows, so only the duration is measured.
 */
export function tapeSpans(tape: readonly RunTapeEvent[]): Span[] {
  const rounds = new Map<string, Span>()
  const calls = new Map<string, Record<string, unknown>>()
  const tools: Span[] = []
  let round: Span | undefined
  for (const e of tape) {
    if (e.event === 'span' && e.data.span) {
      const s = e.data.span as Span
      if (s.kind !== 'llm') continue
      rounds.set(s.id, s)
      round = s
    } else if (e.event === 'tool_call') calls.set(String(e.data.id), e.data)
    else if (e.event === 'tool_result' && round) {
      const id = String(e.data.id)
      const start = round.end ?? round.start
      const wait = Number(e.data.wait_ms) || 0
      tools.push({ id: `t-${id}`, kind: 'tool', name: String(e.data.name ?? calls.get(id)?.name ?? 'tool'), start, end: start + (Number(e.data.duration_ms) || 0), error: e.data.error ? String(e.data.error) : null,
        meta: { arguments: calls.get(id)?.arguments ?? {}, ...(wait > 1000 ? { waited_for_approval_ms: wait } : {}) }, parent_id: round.id })
    }
  }
  return [...rounds.values(), ...tools]
}

/**
 * A worker's model history as chat rows. Each user-role row stands alone (the main agent's task and steers, or the
 * user's own messages); the assistant and tool rows after it fold into one reply, its tool calls as tool events with
 * their results, the way a reply in the main chat carries them. A call with no result yet is pending while `live`.
 */
export function workerRows(messages: SubagentView['messages'], conversationId: string, live: boolean, timings?: ReadonlyMap<string, Timing>): WorkerRow[] {
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
      const said = stripNoReply(text(m.content))  // the worker's NO_REPLY is for the orchestrator, not the user
      if (said) reply.content = reply.content ? `${reply.content}\n\n${said}` : said
      for (const c of m.tool_calls ?? []) {
        const id = c.id ?? `${reply.id}-${reply.tool_events!.length}`
        const done = results.has(id)
        const ev: ToolEvent = { id, name: c.function.name, arguments: parseArgs(c.function.arguments), result_preview: results.get(id) ?? '', duration_ms: timings?.get(id)?.duration_ms ?? 0, wait_ms: timings?.get(id)?.wait_ms, error: null, pending: !done && live }
        reply.tool_events!.push(ev)
      }
    }
  })
  return rows
}
