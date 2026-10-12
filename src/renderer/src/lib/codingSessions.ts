import type { CodingSession, CodingSessionEvent } from '@shared/types'
import { isAttachId } from '@shared/codingAttach'

type Status = CodingSession['status']

/** Still doing, or waiting on, something: the list polls while any session is. */
export const codingLive = (s: Status): boolean => s === 'starting' || s === 'working' || s === 'needs_you'

/** Stop applies to anything still running or stuck (a blocked session's job record may be gone; Stop clears it), not to one still starting. */
export const codingStoppable = (s: Status): boolean => (codingLive(s) && s !== 'starting') || s === 'blocked'

/** The `inbox-dot` tone (app status vocabulary) for a session's status. */
export const codingDot = (s: Status): 'needs-you' | 'working' | 'done' | 'failed' | 'idle' =>
  s === 'needs_you' ? 'needs-you' : s === 'working' || s === 'starting' ? 'working' : s === 'blocked' || s === 'failed' ? 'failed' : s === 'done' ? 'done' : 'idle'

/** The tool-card Badge tone for a session's status. */
export const codingTone = (s: Status): 'ok' | 'warn' | 'bad' | 'run' | undefined =>
  s === 'working' ? 'run' : s === 'needs_you' ? 'warn' : s === 'blocked' || s === 'failed' ? 'bad' : s === 'done' ? 'ok' : undefined

export const codingStatusLabel = (s: Status): string => (s === 'needs_you' ? 'needs you' : s)

export const AGENT_LABEL: Record<CodingSession['agent'], string> = { claude: 'Claude Code', opencode: 'OpenCode', pi: 'pi' }

/** Whether `claude attach` can reach this session: a claude session with a valid attach id. */
export const canAttach = (c: CodingSession): boolean => c.agent === 'claude' && isAttachId(c.external_id)

/** The terminal view a picked session opens straight away; null (nothing until asked) when it cannot attach. */
export const defaultTermMode = (c: CodingSession): 'attach' | null => (canAttach(c) ? 'attach' : null)

/** Events kept per session in the store; older ones are dropped from the front. */
export const MAX_CODING_EVENTS = 500

/** Merge timeline events into a list: one entry per `seq` (a later copy wins), ascending, capped at the newest `cap`. */
export function mergeCodingEvents(existing: CodingSessionEvent[], incoming: CodingSessionEvent[], cap = MAX_CODING_EVENTS): CodingSessionEvent[] {
  if (!incoming.length) return existing
  const bySeq = new Map<number, CodingSessionEvent>()
  for (const e of existing) bySeq.set(e.seq, e)
  for (const e of incoming) bySeq.set(e.seq, e)
  const out = [...bySeq.values()].sort((a, b) => a.seq - b.seq)
  return out.length > cap ? out.slice(out.length - cap) : out
}

/** The newest 'tool' or 'text' event: the "what is it doing now" line. */
export function latestActivity(events: CodingSessionEvent[] | undefined): CodingSessionEvent | null {
  if (!events) return null
  for (let i = events.length - 1; i >= 0; i--) if (events[i].kind === 'tool' || events[i].kind === 'text') return events[i]
  return null
}

/** A progress label (Claude Code reports these as a 'tool' event named "step"): shown as plain text, without the name. */
export const isStepEvent = (e: CodingSessionEvent): boolean => e.kind === 'tool' && e.tool === 'step'

/** One line for an event: a tool call reads "Edit  src/a.ts", a progress step and anything else its text. Whitespace collapses. */
export function eventSummary(e: CodingSessionEvent): string {
  const text = e.text.replace(/\s+/g, ' ').trim()
  return e.kind === 'tool' && e.tool && !isStepEvent(e) ? (text ? `${e.tool}  ${text}` : e.tool) : text
}

/** The last path segment of a worktree, or '' for none. */
export function pathBase(p: string | null | undefined): string {
  return (p ?? '').replace(/[\\/]+$/, '').split(/[\\/]/).pop() ?? ''
}

/** "42s", "5m", "1h 05m": how long a session ran (or has been running). `start`/`end` are unix seconds. */
export function formatElapsed(start: number, end: number): string {
  const s = Math.max(0, Math.round(end - start))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`
}

/** Live sessions first (newest first), then the most recent `recentMax` settled ones. */
export function splitSessions(all: CodingSession[], recentMax = 10): { live: CodingSession[]; recent: CodingSession[] } {
  const byNew = [...all].sort((a, b) => b.created_at - a.created_at)
  // A blocked session's job record is gone; only Stop clears it, so it sits with the live ones.
  const isLive = (c: CodingSession): boolean => codingLive(c.status) || c.status === 'blocked'
  return { live: byNew.filter(isLive), recent: byNew.filter((c) => !isLive(c)).slice(0, recentMax) }
}

export type CodingTransition = 'done' | 'failed' | 'needs_you'

/**
 * Whether a session update is worth telling the user about: it must have been seen before (`prev`) in a different
 * status and now be done, failed or waiting on them. A session first seen already settled, or the same status
 * arriving again, is silent.
 */
export function codingTransition(prev: CodingSession | undefined, next: CodingSession): CodingTransition | null {
  if (!prev || prev.status === next.status) return null
  return next.status === 'done' || next.status === 'failed' || next.status === 'needs_you' ? next.status : null
}

/** The text of the notice for a transition: "<name> finished on <branch>", "needs your input", "failed: <detail>". */
export function codingNoticeText(kind: CodingTransition, c: CodingSession): string {
  if (kind === 'needs_you') return `${c.name} needs your input`
  if (kind === 'failed') return `${c.name} failed${c.detail ? `: ${c.detail}` : ''}`
  const where = c.branch || pathBase(c.worktree)
  return where ? `${c.name} finished on ${where}` : `${c.name} finished`
}
