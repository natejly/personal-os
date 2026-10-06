import type { Attention, Desk, Job, SessionStatus } from '@shared/types'

/**
 * The renderer's copy of backend attention.py: one table for runs, desks and jobs. Both are checked against
 * backend/tests/fixtures/attention_cases.json, so a change to one without the other fails a test.
 */
const RUN: Record<string, Attention> = { running: 'working', awaiting_approval: 'needs_you', error: 'blocked', interrupted: 'blocked' }
const DESK: Record<string, Attention> = {
  planning: 'working', working: 'working',
  awaiting_plan: 'needs_you', needs_approval: 'needs_you', review: 'needs_you',
  blocked: 'blocked', interrupted: 'blocked', failed: 'blocked'
}

export interface AttentionOpts { pending_approvals?: number; pending_proposals?: number; last_error?: string | null; paused_reason?: string | null }

export function attention(kind: 'run' | 'desk' | 'job', status: string | null | undefined, o: AttentionOpts = {}): Attention {
  if (o.pending_approvals || o.pending_proposals) return 'needs_you'
  if (kind === 'job' && (o.paused_reason || o.last_error)) return 'blocked'
  return (kind === 'desk' ? DESK : RUN)[status ?? ''] ?? 'idle'
}

/** A chat's pulse status in run terms. `done` is a verdict that fades back to idle, so it is idle here. */
const SESSION_RUN: Record<SessionStatus, string> = { idle: 'done', done: 'done', working: 'running', 'needs-approval': 'awaiting_approval', error: 'error' }

/** A chat row: a chat working autonomously carries its desk's state, any other chat its own run's. */
export const chatAttention = (pulse: SessionStatus, desk?: Pick<Desk, 'status' | 'attention'>): Attention =>
  desk ? desk.attention ?? attention('desk', desk.status) : attention('run', SESSION_RUN[pulse])

/** GET /jobs carries the state; a row from create/update does not, so it falls back to what the row itself says. */
export const jobAttention = (j: Pick<Job, 'attention' | 'last_error' | 'paused_reason'>): Attention =>
  j.attention ?? attention('job', null, { last_error: j.last_error, paused_reason: j.paused_reason })

/** The "Needs you" filter's order, and what it lets through. */
export const ATTENTION_RANK: Record<Attention, number> = { needs_you: 0, blocked: 1, working: 2, idle: 3 }
export const wantsYou = (a: Attention): boolean => a === 'needs_you' || a === 'blocked'

export const ATTENTION_LABEL: Record<Attention, string> = { idle: 'Idle', working: 'Working', needs_you: 'Needs you', blocked: 'Blocked' }
