import type { Desk, DeskStatus } from '@shared/types'

/** The statuses worth a notification: the desk has stopped, or finished, and the next move is the user's. */
export const NOTIFY_ON: DeskStatus[] = ['awaiting_plan', 'needs_approval', 'blocked', 'interrupted', 'review', 'done', 'failed']

type Row = Pick<Desk, 'id' | 'title' | 'status' | 'status_reason' | 'question' | 'last_error'>

/** The part of the row that says WHY it is in the status, so a desk that asks a second, different question notifies again. */
const reasonOf = (d: Row): string =>
  d.status === 'blocked' ? d.question || d.status_reason || ''
    : d.status === 'failed' ? d.last_error || d.status_reason || ''
      : d.status === 'interrupted' ? d.status_reason || '' : ''

/** One key per desk, status and reason. The same row republished (every flush, and again at the end of a turn) has the same key. */
export const noticeKey = (d: Row): string => `${d.id}\u0000${d.status}\u0000${reasonOf(d)}`

export interface Notice { title: string; body: string; deskId: string }

const label = (d: Row): string => d.title || 'A desk'

/** What to say for a row in a notifying status. */
export function noticeFor(d: Row): Notice | null {
  if (!NOTIFY_ON.includes(d.status)) return null
  const why = reasonOf(d)
  const body =
    d.status === 'awaiting_plan' ? `${label(d)} has a plan to approve.`
      : d.status === 'needs_approval' ? `${label(d)} is waiting for your approval.`
        : d.status === 'blocked' ? (why ? `${label(d)} asks: ${why}` : `${label(d)} needs an answer or an approval.`)
          : d.status === 'interrupted' ? `${label(d)} was interrupted${why ? `: ${why}` : ''}.`
            : d.status === 'review' ? `${label(d)} has output waiting for review.`
              : d.status === 'done' ? `${label(d)} is done.`
                : why ? `${label(d)} failed: ${why}` : `${label(d)} failed.`
  return { title: d.title || 'Working autonomously', body: body.length > 220 ? body.slice(0, 217) + '…' : body, deskId: d.id }
}

/**
 * Walk the desks once and return the notices that are new. `last` maps desk -> the key it was last seen with
 * and is updated in place for EVERY status, not only notifying ones: a desk that goes needs_approval -> working
 * -> needs_approval is a new transition and notifies again, while a republished row does not. `seeding` records
 * without notifying, so desks already waiting when the app launches are history.
 */
export function collectNotices(last: Map<string, string>, desks: Row[], seeding: boolean): Notice[] {
  const out: Notice[] = []
  for (const d of desks) {
    const key = noticeKey(d)
    if (last.get(d.id) === key) continue
    last.set(d.id, key)
    if (seeding) continue
    const n = noticeFor(d)
    if (n) out.push(n)
  }
  return out
}
