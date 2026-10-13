import type { Message, Span, ToolEvent } from '@shared/types'

export interface TraceTool { span: Span; event: ToolEvent | null }
export interface TraceRound { round: number; llm: Span | null; tools: TraceTool[] }
export interface TraceTurn { messageId: string; model: string | null; thinking: string | null; rounds: TraceRound[]; other: Span[]; live: boolean }

/**
 * One assistant reply's spans grouped per model round: the model call, the tool calls under it (each paired with its
 * ToolEvent for the result), and the rest (context, compaction, auto-learn). Tool events pair with tool spans by name,
 * in call order.
 */
export function groupTurn(m: Message, live = false): TraceTurn {
  const spans = [...(m.trace ?? [])].sort((a, b) => a.start - b.start)
  const queue = new Map<string, ToolEvent[]>()
  for (const e of m.tool_events ?? []) queue.set(e.name, [...(queue.get(e.name) ?? []), e])
  const rounds = new Map<number, TraceRound>()
  const get = (n: number): TraceRound => rounds.get(n) ?? (rounds.set(n, { round: n, llm: null, tools: [] }), rounds.get(n)!)
  const llmRound = new Map<string, number>()
  const other: Span[] = []
  for (const s of spans) {
    if (s.kind === 'llm') { const n = Number(s.meta.round ?? rounds.size + 1); llmRound.set(s.id, n); get(n).llm = s }
    else if (s.kind === 'tool') {
      const n = (s.parent_id && llmRound.get(s.parent_id)) || Number(s.meta.round ?? 0)
      get(n).tools.push({ span: s, event: queue.get(s.name)?.shift() ?? null })
    } else other.push(s)
  }
  return { messageId: m.id, model: m.model, thinking: m.reasoning ?? null, rounds: [...rounds.values()].sort((a, b) => a.round - b.round), other, live }
}

/** Every assistant reply in the chat that recorded a trace, oldest first. */
export const traceTurns = (messages: Message[], liveId?: string | null): TraceTurn[] =>
  messages.filter((m) => m.role === 'assistant' && m.trace?.length).map((m) => groupTurn(m, m.id === liveId))
