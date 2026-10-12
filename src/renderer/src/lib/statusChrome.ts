import type { PlanRecord, PlanRecordStep, PlanStep } from '@shared/types'
import { planSummary } from './plan'

/**
 * What a chat shows about work in progress. The main agent gets no generic status chrome: no "Working" strip, no face
 * line, no turn or round line. The one live line a reply may carry names something specific (the tool in flight, a retry
 * countdown, the worker it handed off to, an approval it waits on; see `busyText`), never just "working". The composer's
 * Stop is its control and the reply itself is its progress. Two cards sit above the composer, both about something specific: the workers card (only while background workers run) and the
 * progress card (only for a multi-step checklist or plan, see `progressCardShown`).
 */

/** The workers card above the composer: only while one is live. Finished workers appear in it only on request ("Show finished"). */
export const workersCardShown = (liveWorkers: number): boolean => liveWorkers > 0

/** A plan in the transcript only while it waits for the user's approval (that is a question, not a status). Once approved
 *  its progress shows in the progress card above the composer (ProgressCard). */
export const inlinePlanShown = (plan: { status: string } | null | undefined): boolean => plan?.status === 'pending'

export type Progress = { done: number; total: number; current: string | null; allDone: boolean }
/** What the progress card reads: the agent's own checklist (`todo_write`), or else the decided plan of a desk. */
export type ProgressSource = { kind: 'checklist' | 'plan'; summary: Progress }

/** The checklist wins when it has steps. Otherwise a decided (not pending) desk plan, counting only its live steps
 *  (dropped and rejected ones were never going to happen); its current step is the first live one not done. */
export function progressSource(checklist: Pick<PlanStep, 'text' | 'status'>[] | undefined, plan: Pick<PlanRecord, 'status'> & { steps: Pick<PlanRecordStep, 'status' | 'title' | 'tool'>[] } | null | undefined): ProgressSource | null {
  if (checklist?.length) return { kind: 'checklist', summary: planSummary(checklist) }
  if (!plan || plan.status === 'pending') return null
  const live = plan.steps.filter((s) => s.status !== 'dropped' && s.status !== 'rejected')
  if (!live.length) return null
  const done = live.filter((s) => s.status === 'done').length
  const open = live.find((s) => s.status !== 'done')
  return { kind: 'plan', summary: { done, total: live.length, current: open ? open.title || open.tool || 'Think it through' : null, allDone: done === live.length } }
}

/** The progress card above the composer: only for multi-step work (a single step is not worth a card), and a finished
 *  plan from an old turn does not sit there for good: its "all done" state shows only while the reply is still streaming. */
export const progressCardShown = (p: Progress, streaming: boolean): boolean => p.total >= 2 && (!p.allDone || streaming)
