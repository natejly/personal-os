import type { Message, MessageStatus, SubagentInfo } from '@shared/types'
import { modelLabel } from './modelLabel'
import { fullTitle } from './toolDisplay'

const CAUSE: Record<NonNullable<MessageStatus['reason']>, string> = {
  rate_limit: 'The provider is rate-limiting.',
  provider_error: 'The provider returned an error.',
  connection: 'Could not reach the provider.'
}

/** The one line shown under a streaming reply that has nothing to show yet. `nowMs` is passed in so the countdown is testable. */
export const statusText = (status: MessageStatus, nowMs: number): string => {
  if (status.kind === 'compacting') return 'Summarizing earlier messages to make room…'
  if (status.kind === 'route') return `Auto → ${modelLabel(status.model ?? '')}: ${status.why ?? ''}`
  const left = status.until ? Math.ceil((status.until - nowMs) / 1000) : 0
  if (left <= 0) return 'Retrying now…'
  const of = status.attempt && status.max ? ` (attempt ${status.attempt} of ${status.max})` : ''
  return `${CAUSE[status.reason ?? 'provider_error']} Retrying in ${left}s${of}`
}

/** Whether the line changes on its own (a countdown), so the view only runs a timer while it must. */
export const statusTicks = (status: MessageStatus, nowMs: number): boolean => status.kind === 'retry' && !!status.until && status.until > nowMs

/** What a bare wait says after `ms` with nothing streamed: nothing for the first 5s, then the elapsed time. */
export const waitText = (ms: number): string | null => {
  const s = Math.floor(ms / 1000)
  if (s < 5) return null
  return s < 30 ? `Thinking… ${s}s` : `Still waiting on the model… ${s}s`
}

/** The latest complete sentence of a thinking stream, or its unfinished tail, cut to one line. */
export const reasoningTail = (text: string): string => {
  const flat = text.replace(/[*_`#>]+/g, '').replace(/\s+/g, ' ').trim()
  if (!flat) return ''
  const parts = flat.split(/(?<=[.!?])\s+/)
  const tail = (parts.length > 1 && !/[.!?]$/.test(flat) ? parts[parts.length - 2] : parts[parts.length - 1]).trim()
  return tail.length > 90 ? `${tail.slice(0, 89).trimEnd()}…` : tail
}

/**
 * What a reply is doing right now, in one line: the tool call in flight, the subagents it is waiting on, or
 * the latest sentence of its thinking. Null once the answer itself is streaming or nothing is known yet.
 */
// ponytail: the thinking line is the reasoning stream's last sentence, not a model-written summary; add a
// small-model summarizer on the backend if the raw sentence reads badly for the models in use.
export const nowText = (m: Pick<Message, 'reasoning' | 'tool_events' | 'content'> | null | undefined, subs: Record<string, SubagentInfo> = {}): string | null => {
  if (!m || m.content) return null
  const running = Object.values(subs).filter((k) => k.state === 'running')
  const pending = (m.tool_events ?? []).filter((t) => t.pending)
  const call = pending[pending.length - 1]
  if (call && running.length && /^agent_(spawn|wait)$/.test(call.name)) return `Waiting on ${running.length} subagent${running.length === 1 ? '' : 's'}`
  if (call) return fullTitle(call.name, call.arguments)
  if (running.length) return `Running ${running.length} subagent${running.length === 1 ? '' : 's'}`
  const tail = m.reasoning ? reasoningTail(m.reasoning) : ''
  return tail || null
}
