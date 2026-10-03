import type { ConversationUsage } from '@shared/types'

/** Pure formatting for the per-chat usage line and the transcript's day dividers. No React, no store. */

export const fmtTokens = (n: number): string =>
  new Intl.NumberFormat('en-US', { notation: n >= 100000 ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(n)

export const fmtCost = (n: number): string =>
  n === 0 ? '$0' : n < 0.01 ? `$${n.toFixed(4)}` : n < 1 ? `$${n.toFixed(3)}` : `$${n.toFixed(2)}`

/** "This chat: 12.4k tokens · $0.042". Null when nothing was recorded. A call with no known price is counted, never costed at $0. */
export const usageLine = (u: ConversationUsage): string | null => {
  const t = u.totals
  if (!t.calls) return null
  const parts = [`${fmtTokens(t.tokens)} tokens`]
  const priced = t.calls - t.unpriced
  if (priced > 0) parts.push(fmtCost(t.cost))
  if (t.unpriced > 0) parts.push(priced > 0 ? `${t.unpriced} unpriced` : 'unpriced')
  return `This chat: ${parts.join(' · ')}`
}

/** Local calendar day, "2026-10-02". */
export const dayKey = (ts: number): string => {
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** "Today", "Yesterday", else "Mon, Sep 29" (with the year when it is not this one). `now` is epoch seconds. */
export const dayLabel = (ts: number, now: number = Date.now() / 1000): string => {
  const key = dayKey(ts)
  if (key === dayKey(now)) return 'Today'
  const back = new Date(now * 1000)
  back.setDate(back.getDate() - 1)
  if (key === dayKey(back.getTime() / 1000)) return 'Yesterday'
  const d = new Date(ts * 1000)
  const sameYear = d.getFullYear() === new Date(now * 1000).getFullYear()
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', ...(sameYear ? {} : { year: 'numeric' }) })
}

/** Absolute clock time. Never relative: a memoised message does not re-render on a timer, so "just now" would go stale. */
export const clockTime = (ts: number): string => new Date(ts * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })

export const fullTime = (ts: number): string => new Date(ts * 1000).toLocaleString()
