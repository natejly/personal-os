/*
 * One date vocabulary for every list: Today / Tomorrow / a weekday inside the week / "Oct 11" beyond
 * it, with the year only when it is not this one. Numeric dates (10/11/2026) read differently by
 * locale and are never used.
 */

const DAY_MS = 86_400_000
const midnight = (d: Date): number => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()

/** Whole calendar days from `now` to `d`, local time. Negative is the past. */
export const dayDiff = (d: Date, now: Date = new Date()): number => Math.round((midnight(d) - midnight(now)) / DAY_MS)

/** "Oct 11", or "Oct 11, 2025" outside the current year. */
export const shortDate = (d: Date, now: Date = new Date()): string =>
  d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', ...(d.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {}) })

/** "Fri, Oct 2, 9:14 AM": a full timestamp without the seconds nobody reads. */
export const shortDateTime = (d: Date, now: Date = new Date()): string =>
  d.toLocaleString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
    ...(d.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {})
  })

/** A span of days as one label ("Sep 28 – Oct 4"), with the year when either end is outside this one. */
export const rangeLabel = (from: Date, to: Date, now: Date = new Date()): string => {
  const year = from.getFullYear() !== now.getFullYear() || to.getFullYear() !== now.getFullYear()
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', ...(year ? { year: 'numeric' } : {}) }).formatRange(from, to)
}

/** A due date (YYYY-MM-DD) as a chip label, plus the class that colours it by urgency. */
export const dueLabel = (due: string | null, now: Date = new Date()): { text: string; cls: string } => {
  if (!due) return { text: '', cls: '' }
  const d = new Date(due + 'T00:00:00')
  const diff = dayDiff(d, now)
  if (diff < 0) return { text: `${-diff}d overdue`, cls: 'overdue' }
  if (diff === 0) return { text: 'Today', cls: 'today' }
  if (diff === 1) return { text: 'Tomorrow', cls: '' }
  if (diff < 7) return { text: d.toLocaleDateString(undefined, { weekday: 'short' }), cls: '' }
  return { text: shortDate(d, now), cls: '' }
}
