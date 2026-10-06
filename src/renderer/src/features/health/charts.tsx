/**
 * The two Health charts. One series each, in the accent colour; ink stays in text tokens.
 * - Sparkline: a tile's last couple of weeks. Bars for metrics whose days add up (sum) or are yes/no,
 *   a line for readings (weight, mood), so the form matches what a day's number means.
 * - TrendChart: the selected metric over the chosen range, with a dashed goal line, a crosshair that
 *   snaps to the nearest day, and arrow-key stepping for the same readout without a pointer.
 * Days with nothing logged stay empty (a gap in the line, no bar); they are never drawn as zero.
 */
import { useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import type { HealthSummary } from '@shared/types'
import { dayLabel, fmt, goalText, num, shortDay, ticks } from './format'

type Point = { day: string; value: number | null }
const asBars = (m: Pick<HealthSummary, 'agg' | 'kind'>): boolean => m.agg === 'sum' || m.kind === 'check'

/** y-range: bars and yes/no start at zero; 1-5 scales are fixed; readings hug their data. */
function yDomain(m: HealthSummary, values: number[]): [number, number] {
  if (m.kind === 'check') return [0, 1]
  if (m.kind === 'scale') return [1, 5]
  const withGoal = m.goal != null ? [...values, m.goal] : values
  if (!withGoal.length) return [0, 1]
  const hi = Math.max(...withGoal)
  if (asBars(m)) return [0, hi > 0 ? hi * 1.08 : 1]
  const lo = Math.min(...withGoal)
  const pad = Math.max((hi - lo) * 0.15, Math.abs(hi) * 0.02, Math.pow(10, -m.decimals))
  return [lo - pad, hi + pad]
}

/** Line path with a break at every unlogged day. */
function linePath(pts: Point[], x: (i: number) => number, y: (v: number) => number): string {
  let d = '', pen = false
  pts.forEach((p, i) => {
    if (p.value == null) { pen = false; return }
    d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`
    pen = true
  })
  return d
}

/** A bar whose data end (the top) is rounded and whose baseline end is square. */
function barPath(x: number, w: number, top: number, base: number): string {
  const h = base - top
  if (h <= 0) return ''
  const r = Math.min(4, w / 2, h)
  return `M${x},${base}V${top + r}Q${x},${top} ${x + r},${top}H${x + w - r}Q${x + w},${top} ${x + w},${top + r}V${base}Z`
}

export function Sparkline({ metric, points }: { metric: HealthSummary; points: Point[] }): JSX.Element {
  const W = 120, H = 28
  const vals = points.flatMap((p) => (p.value == null ? [] : [p.value]))
  const [lo, hi] = yDomain(metric, vals)
  const y = (v: number): number => H - 1 - ((v - lo) / (hi - lo || 1)) * (H - 2)
  const label = `${metric.label}, last ${points.length} days`
  if (asBars(metric)) {
    const slot = W / points.length, w = Math.max(1, slot - 2)
    return (
      <svg className="hl-spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
        {points.map((p, i) => {
          if (p.value == null) return null
          // A logged "no" or zero still shows as a stub, so it reads differently from not logged.
          const top = Math.min(y(p.value), H - 2)
          return <path key={p.day} d={barPath(i * slot + 1, w, top, H)} className="hl-mark" />
        })}
      </svg>
    )
  }
  const x = (i: number): number => (points.length === 1 ? W / 2 : 2 + (i / (points.length - 1)) * (W - 4))
  const lone = points.map((p, i) => (p.value != null && points[i - 1]?.value == null && points[i + 1]?.value == null ? i : -1)).filter((i) => i >= 0)
  return (
    <svg className="hl-spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
      <path d={linePath(points, x, y)} className="hl-line" vectorEffect="non-scaling-stroke" />
      {lone.map((i) => <line key={i} x1={x(i)} x2={x(i)} y1={y(points[i].value!)} y2={y(points[i].value!)} className="hl-dot-line" vectorEffect="non-scaling-stroke" />)}
    </svg>
  )
}

const M = { top: 12, right: 12, bottom: 24, left: 44 }

export function TrendChart({ metric, today }: { metric: HealthSummary; today: string }): JSX.Element {
  const wrap = useRef<HTMLDivElement>(null)
  const [W, setW] = useState(600)
  const [hover, setHover] = useState<number | null>(null)
  useLayoutEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setW(Math.max(240, Math.round(e.contentRect.width))))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const pts = metric.series
  const H = metric.kind === 'check' ? 92 : 200
  const iw = W - M.left - M.right, ih = H - M.top - M.bottom
  const vals = pts.flatMap((p) => (p.value == null ? [] : [p.value]))
  const [lo, hi] = yDomain(metric, vals)
  const y = (v: number): number => M.top + ih - ((v - lo) / (hi - lo || 1)) * ih
  const slot = iw / pts.length
  const cx = (i: number): number => M.left + slot * (i + 0.5)
  const bars = asBars(metric)
  const goal = metric.goal != null && metric.kind !== 'check' ? metric.goal : null

  const pick = (clientX: number): void => {
    const r = wrap.current?.getBoundingClientRect()
    if (!r) return
    const i = Math.floor((clientX - r.left - M.left) / slot)
    setHover(Math.max(0, Math.min(pts.length - 1, i)))
  }
  const onKey = (e: KeyboardEvent): void => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight' && e.key !== 'Home' && e.key !== 'End') return
    e.preventDefault()
    const last = pts.length - 1
    setHover((h) => {
      if (e.key === 'Home') return 0
      if (e.key === 'End') return last
      const cur = h ?? last
      return Math.max(0, Math.min(last, cur + (e.key === 'ArrowLeft' ? -1 : 1)))
    })
  }

  const yTicks = metric.kind === 'check' ? [] : ticks(lo, hi, 4).filter((t) => t >= lo && t <= hi)
  const xIdx = [0, Math.floor((pts.length - 1) / 2), pts.length - 1].filter((v, i, a) => a.indexOf(v) === i)
  const h = hover != null ? pts[hover] : null
  const tipLeft = hover != null ? Math.min(Math.max(cx(hover), 70), W - 70) : 0

  return (
    <div className="hl-trend" ref={wrap}>
      <svg width={W} height={H} tabIndex={0} className="hl-trend-svg"
        role="img" aria-label={`${metric.label} over ${pts.length} days. Use the arrow keys to read each day.`}
        onPointerMove={(e) => pick(e.clientX)} onPointerLeave={() => setHover(null)}
        onKeyDown={onKey} onBlur={() => setHover(null)}>
        {yTicks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} className="hl-grid" />
            <text x={M.left - 8} y={y(t)} className="hl-axis" textAnchor="end" dominantBaseline="middle">{num(metric, t)}</text>
          </g>
        ))}
        {xIdx.map((i) => (
          <text key={i} x={cx(i)} y={H - 6} className="hl-axis" textAnchor={i === 0 ? 'start' : i === pts.length - 1 ? 'end' : 'middle'}>
            {pts[i].day === today ? 'Today' : shortDay(pts[i].day)}
          </text>
        ))}
        {metric.kind === 'check'
          ? pts.map((p, i) => (
              <rect key={p.day} x={cx(i) - Math.max(1, slot - 2) / 2} y={M.top + 6} width={Math.max(1, slot - 2)} height={ih - 12} rx={Math.min(4, slot / 3)}
                className={p.value == null ? 'hl-cell-empty' : p.value >= 1 ? 'hl-mark' : 'hl-cell-no'} />
            ))
          : bars
            ? pts.map((p, i) => p.value == null ? null : (
                <path key={p.day} d={barPath(cx(i) - Math.max(1, slot - 2) / 2, Math.max(1, slot - 2), Math.min(y(p.value), M.top + ih - 2), M.top + ih)}
                  className={`hl-mark ${hover === i ? 'hover' : ''}`} />
              ))
            : (
              <>
                <path d={linePath(pts, cx, y)} className="hl-line" />
                {pts.map((p, i) => p.value != null && (pts[i - 1]?.value == null || pts[i + 1]?.value == null || hover === i)
                  ? <circle key={p.day} cx={cx(i)} cy={y(p.value)} r={4} className="hl-dot" />
                  : null)}
              </>
            )}
        {goal != null && goal >= lo && goal <= hi && (
          <g>
            <line x1={M.left} x2={W - M.right} y1={y(goal)} y2={y(goal)} className="hl-goal" />
            <text x={W - M.right} y={y(goal) - 5} className="hl-axis" textAnchor="end">{goalText(metric)}</text>
          </g>
        )}
        {hover != null && <line x1={cx(hover)} x2={cx(hover)} y1={M.top} y2={M.top + ih} className="hl-crosshair" />}
      </svg>
      {h && (
        <div className="hl-tip" style={{ left: tipLeft }} role="status">
          <span className="hl-tip-day">{dayLabel(h.day, today)}</span>
          <strong>{h.value == null ? 'Not logged' : fmt(metric, h.value)}</strong>
        </div>
      )}
    </div>
  )
}
