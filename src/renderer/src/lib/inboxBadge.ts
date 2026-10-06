import type { AgentInbox } from '@shared/types'

/** The Today badge: everything waiting on the user plus job runs not yet read. The daily digest is listed unread
 *  but never counted: it is the quiet summary, not something waiting. */
export const inboxBadge = (box: AgentInbox | null): number =>
  box ? box.counts.needs_you + box.while_you_were_away.filter((r) => !r.seen && r.kind !== 'digest').length : 0

/** The inbox with `runIds` (every run when null) marked read, for an optimistic update before the round trip. */
export function markRunsSeen(box: AgentInbox, runIds: string[] | null): AgentInbox {
  const hit = (id: string): boolean => runIds === null || runIds.includes(id)
  const away = box.while_you_were_away.map((r) => (hit(r.run_id) ? { ...r, seen: true } : r))
  return { ...box, while_you_were_away: away, counts: { ...box.counts, unseen_runs: away.filter((r) => !r.seen).length } }
}
