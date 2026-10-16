/** Pure helpers shared by the Health view, its charts and the Today card. No React, so they unit-test. */
import type { HealthMetric } from '@shared/types'

type Shape = Pick<HealthMetric, 'kind' | 'unit' | 'decimals'>
type Goal = Pick<HealthMetric, 'goal' | 'goal_dir'>

/** The bare number at the metric's precision, trailing zeros dropped (7.0 h reads "7"). */
export function num(m: Pick<HealthMetric, 'decimals'>, v: number): string {
  return Number(v.toFixed(m.decimals)).toLocaleString(undefined, { maximumFractionDigits: m.decimals })
}

/** A value as a reader says it: "7.5 h", "8,000 steps", "4/5", "Yes". */
export function fmt(m: Shape, v: number | null | undefined): string {
  if (v == null) return '—'
  if (m.kind === 'check') return v >= 1 ? 'Yes' : 'No'
  if (m.kind === 'scale' || m.unit === '/5') return `${num(m, v)}/5`
  return m.unit ? `${num(m, v)} ${m.unit}` : num(m, v)
}

export function meets(m: Goal, v: number | null | undefined): boolean | null {
  if (v == null || m.goal == null || !m.goal_dir) return null
  return m.goal_dir === 'at_least' ? v >= m.goal : v <= m.goal
}

/** Fill for a goal meter, 0..1. An at-most goal fills as you approach the cap, like a budget. */
export function progress(m: Goal, v: number | null | undefined): number | null {
  if (m.goal == null || !m.goal_dir || m.goal <= 0) return null
  return Math.max(0, Math.min(1, (v ?? 0) / m.goal))
}

export function goalText(m: Shape & Goal): string | null {
  if (m.goal == null || !m.goal_dir) return null
  if (m.kind === 'check') return m.goal_dir === 'at_least' ? 'Goal: daily' : null
  return `${m.goal_dir === 'at_least' ? 'Goal' : 'Limit'} ${fmt(m, m.goal)}`
}

/** Change between two averages as "+0.4 h" / "−1,200 steps", or null when either side is missing. */
export function delta(m: Shape, now: number | null, before: number | null): string | null {
  if (now == null || before == null || m.kind === 'check') return null
  const d = now - before
  if (Math.abs(d) < Math.pow(10, -m.decimals) / 2) return 'no change'
  const s = num(m, Math.abs(d))
  return `${d > 0 ? '+' : '−'}${m.kind === 'scale' ? s : m.unit ? `${s} ${m.unit}` : s}`
}

/** Local YYYY-MM-DD n days from `day` (negative for earlier). Noon avoids DST edges. */
export function shiftDay(day: string, n: number): string {
  const d = new Date(`${day}T12:00:00`)
  d.setDate(d.getDate() + n)
  const p = (x: number): string => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/** "Today", "Yesterday", or "Mon, Sep 28" relative to `today`. */
export function dayLabel(day: string, today: string): string {
  if (day === today) return 'Today'
  if (day === shiftDay(today, -1)) return 'Yesterday'
  return new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}

export const shortDay = (day: string): string =>
  new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

/** Rounded "nice" ticks covering [lo, hi], about `count` of them. */
export function ticks(lo: number, hi: number, count = 4): number[] {
  if (!(hi > lo)) return [lo]
  const raw = (hi - lo) / count
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= raw) ?? raw
  const out: number[] = []
  for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9; t += step) out.push(Number(t.toFixed(10)))
  return out
}
