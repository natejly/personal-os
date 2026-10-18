import type { Message, MessageStatus, SubagentInfo, ToolEvent, WorkerInfo } from '@shared/types'
import { modelLabel } from './modelLabel'
import { fullTitle, staysVisible } from './toolDisplay'
import { workerIsLive } from './workers'

const CAUSE: Record<NonNullable<MessageStatus['reason']>, string> = {
  rate_limit: 'The provider is rate-limiting.',
  provider_error: 'The provider returned an error.',
  connection: 'Could not reach the provider.'
}

/** The one line shown under a streaming reply that has nothing to show yet. `nowMs` is passed in so the countdown is testable. */
export const statusText = (status: MessageStatus, nowMs: number): string => {
  if (status.kind === 'compacting') return 'Summarizing earlier messages to make room…'
  if (status.kind === 'phase') return status.text ?? ''
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

/**
 * What a reply is doing right now, in one line: the tool call in flight or the subagents it is waiting on.
 * Null once the answer itself is streaming or nothing is known yet.
 */
export const nowText = (m: Pick<Message, 'reasoning' | 'tool_events' | 'content'> | null | undefined, subs: Record<string, SubagentInfo> = {}): string | null => {
  if (!m || m.content) return null
  const running = Object.values(subs).filter((k) => k.state === 'running')
  const pending = (m.tool_events ?? []).filter((t) => t.pending)
  const call = pending[pending.length - 1]
  if (call && running.length && /^agent_(spawn|wait)$/.test(call.name)) return `Waiting on ${running.length} subagent${running.length === 1 ? '' : 's'}`
  if (call) return fullTitle(call.name, call.arguments)
  if (running.length) return `Running ${running.length} subagent${running.length === 1 ? '' : 's'}`
  // The thinking summary is never a status: reasoning text does not render anywhere.
  return null
}

/** " 12s" once a running call has gone 5s, else "": short calls stay quiet, slow ones do not look hung. */
export const elapsedSuffix = (ms: number): string => {
  const s = Math.floor(ms / 1000)
  return s < 5 ? '' : ` ${s}s`
}

/** Calls whose card already prints its own "Running…" while it is on screen. */
const CARD_RUNNING = /^(shell_run|shell_poll|coding_session_(start|list|status|send|stop|diff))$/

const HANDOFF: Record<string, string> = {
  message_worker: 'Messaging a worker',
  check_worker: 'Checking on a worker',
  stop_worker: 'Stopping a worker',
  resume_worker: 'Resuming a worker'
}

const asText = (v: unknown): string => (typeof v === 'string' ? v.trim() : '')
const short = (t: string, n = 60): string => (t.length > n ? `${t.slice(0, n - 1).trimEnd()}…` : t)

/** What a hand-off call is doing, naming the worker when the arguments or the workers list say which. */
function handoffText(call: ToolEvent, workers: readonly WorkerInfo[]): string {
  if (call.name === 'delegate') {
    const name = short(asText(call.arguments?.title) || asText(call.arguments?.goal))
    return name ? `Delegating to a worker: ${name}…` : 'Delegating to a worker…'
  }
  const w = workers.find((x) => x.id === asText(call.arguments?.worker_id))
  return `${HANDOFF[call.name]}${w?.title ? `: ${short(w.title)}` : ''}…`
}

export type BusyContext = { nowMs: number; subs?: Record<string, SubagentInfo>; workers?: readonly WorkerInfo[] }

/**
 * The one named line under a streaming reply that is busy without streaming text: a retry or compaction wait, a call
 * waiting for approval, a hand-off, subagents, a tool in flight, or workers the reply is waiting on. Unlike `nowText`
 * it does not go quiet once the answer has started, because a tool can run mid-answer. Whether it is null never
 * depends on `nowMs` (only the countdown and elapsed time in the text do).
 * Order: status, approval, hand-off, subagents, tool call, live workers (only while no text has streamed).
 */
/** The busy line while a card waits on the user. */
export const APPROVAL_WAIT = 'Waiting for your approval'

export function busyText(m: (Pick<Message, 'status' | 'tool_events'> & Partial<Pick<Message, 'content'>>) | null | undefined, ctx: BusyContext): string | null {
  if (!m) return null
  if (m.status) return statusText(m.status, ctx.nowMs)
  const workers = ctx.workers ?? []
  const pending = (m.tool_events ?? []).filter((t) => t.pending)
  const call = pending[pending.length - 1]
  if (pending.some((t) => t.needs_approval)) return APPROVAL_WAIT
  if (call && (call.name === 'delegate' || call.name in HANDOFF)) return handoffText(call, workers)
  const running = Object.values(ctx.subs ?? {}).filter((k) => k.state === 'running')
  const n = running.length
  if (call && n && /^agent_(spawn|wait)$/.test(call.name)) return `Waiting on ${n} subagent${n === 1 ? '' : 's'}`
  if (call) {
    // A card on screen already says it is running; the same words twice would read as two things happening.
    if (CARD_RUNNING.test(call.name) && staysVisible(call)) return null
    return `${fullTitle(call.name, call.arguments)}…${call.started_at ? elapsedSuffix(ctx.nowMs - call.started_at) : ''}`
  }
  if (n) return `Running ${n} subagent${n === 1 ? '' : 's'}`
  // Workers are only waited on in silence: while text is streaming the line would be noise.
  const live = m.content ? [] : workers.filter(workerIsLive)
  if (live.length === 1) return live[0].now ? `Worker: ${short(live[0].now, 80)}` : `Waiting on a worker: ${short(live[0].title)}`
  if (live.length) return `Waiting on ${live.length} workers`
  return null
}
