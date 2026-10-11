import type { AgentInbox } from '@shared/types'

/** The Today badge: everything waiting on the user plus job runs not yet read. */
export const inboxBadge = (box: AgentInbox | null): number =>
  box ? box.counts.needs_you + box.while_you_were_away.filter((r) => !r.seen).length : 0

/** The inbox with `runIds` (every run when null) marked read, for an optimistic update before the round trip. */
export function markRunsSeen(box: AgentInbox, runIds: string[] | null): AgentInbox {
  const hit = (id: string): boolean => runIds === null || runIds.includes(id)
  const away = box.while_you_were_away.map((r) => (hit(r.run_id) ? { ...r, seen: true } : r))
  return { ...box, while_you_were_away: away, counts: { ...box.counts, unseen_runs: away.filter((r) => !r.seen).length } }
}

/** The inbox with `runIds` removed, for an optimistic delete before the round trip. The run counts are
 *  recomputed from what is left; needs_you is not touched (a run message is not a pending decision). */
export function withoutInboxRuns(box: AgentInbox, runIds: string[]): AgentInbox {
  const away = box.while_you_were_away.filter((r) => !runIds.includes(r.run_id))
  return {
    ...box, while_you_were_away: away,
    counts: {
      ...box.counts, runs: away.length, unseen_runs: away.filter((r) => !r.seen).length,
      late: away.filter((r) => r.late).length,
      failed: away.filter((r) => r.status === 'error' || r.status === 'interrupted').length
    }
  }
}
