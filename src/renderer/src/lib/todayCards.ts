import type { PlannerBlock } from '@shared/types'

export const blockKey = (b: PlannerBlock): string => `${b.todo_id}:${b.start}`
export const blockWhen = (b: PlannerBlock): string => {
  const s = new Date(b.start), e = new Date(b.end)
  return `${s.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })} ${s.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}–${e.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`
}

/** The mail-watch counts as display lines; zero counts are left out. */
export function mailWatchLines(m: { to_reply: number; awaiting_reply_overdue: number } | undefined): string[] {
  if (!m) return []
  return [
    ...(m.to_reply > 0 ? [`${m.to_reply} to reply`] : []),
    ...(m.awaiting_reply_overdue > 0 ? [`${m.awaiting_reply_overdue} waiting on a reply`] : [])
  ]
}

/** The blocks the user left ticked; only these are ever sent to /planner/apply. */
export const pickedBlocks = (blocks: PlannerBlock[], picked: Set<string>): PlannerBlock[] => blocks.filter((b) => picked.has(blockKey(b)))
