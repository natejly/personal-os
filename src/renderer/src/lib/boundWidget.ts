/**
 * Pure helpers for declarative dashboard widgets (chart | stat | table), kept out of the React component so
 * `boundWidget.test.ts` can run them under node:test. The backend (widget_spec.py) already fetched, shaped and
 * cached the rows; all that is left here is turning a stored spec plus rows into what the trusted components take.
 */
import type { Widget } from '@shared/types'

export const DECLARATIVE_KINDS = ['chart', 'stat', 'table'] as const
export type DeclarativeKind = (typeof DECLARATIVE_KINDS)[number]

export const isDeclarative = (kind: string): kind is DeclarativeKind => (DECLARATIVE_KINDS as readonly string[]).includes(kind)

type Row = Record<string, unknown>
interface BoundSpec {
  select?: { x?: string; y?: string | string[] }
  chart?: { type?: string; stacked?: boolean; unit?: string; title?: string }
  stat?: { label?: string; unit?: string }
  table?: { columns?: { key?: string; label?: string }[] }
}
interface BoundData { rows?: Row[]; stat?: { value: number; label: string; unit: string; delta: number | null } | null }

const spec = (w: Pick<Widget, 'spec'>): BoundSpec => (w.spec && typeof w.spec === 'object' ? (w.spec as BoundSpec) : {})
export const boundRows = (w: Pick<Widget, 'data'>): Row[] => {
  const d = w.data as BoundData | null | undefined
  return Array.isArray(d?.rows) ? (d?.rows as Row[]) : []
}
export const boundStat = (w: Pick<Widget, 'data'>): BoundData['stat'] => (w.data as BoundData | null | undefined)?.stat ?? null

/**
 * The JSON a ```chart block would carry, so ChartBlock's own `parseSpec` (numeric coercion, series
 * filtering, the 500-row cap) builds the chart and the widget cannot drift from chat charts.
 * `null` when there is nothing to draw yet.
 */
export function boundChartSource(w: Pick<Widget, 'spec' | 'data' | 'title'>): string | null {
  const rows = boundRows(w)
  const s = spec(w)
  const x = s.select?.x
  const y = s.select?.y
  const series = typeof y === 'string' ? [y] : Array.isArray(y) ? y : []
  if (!rows.length || !x || !series.length) return null
  return JSON.stringify({
    type: s.chart?.type ?? 'bar',
    title: s.chart?.title ?? '',
    x,
    series,
    data: rows,
    stacked: s.chart?.stacked === true,
    unit: s.chart?.unit ?? ''
  })
}

/** Table columns: the spec's list, or every key of the first row (minus the internal `_key`) when it names none. */
export function boundColumns(w: Pick<Widget, 'spec' | 'data'>): { key: string; label: string }[] {
  const listed = (spec(w).table?.columns ?? []).filter((c): c is { key: string; label?: string } => typeof c?.key === 'string')
  if (listed.length) return listed.map((c) => ({ key: c.key, label: c.label || c.key }))
  const first = boundRows(w)[0] ?? {}
  return Object.keys(first).filter((k) => k !== '_key').slice(0, 12).map((k) => ({ key: k, label: k }))
}

/** `+6`, `-2.5`, or '' for no delta. The sign is the point, so zero reads as `0`. */
export const fmtDelta = (d: number | null | undefined): string => {
  if (d === null || d === undefined || !Number.isFinite(d)) return ''
  const s = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(Math.abs(d))
  return d > 0 ? `+${s}` : d < 0 ? `-${s}` : '0'
}

/**
 * A chat chart's parsed spec as a widget spec with its rows stored inline (static data, never refetched),
 * for `POST /dashboards/{id}/widgets`. The mirror of `boundChartSource`.
 */
export function chartToWidgetSpec(c: {
  type: string; title: string; x: string; series: { key: string }[]; data: Row[]; stacked: boolean; unit: string
}): Record<string, unknown> {
  return {
    kind: 'chart',
    inline_rows: c.data,
    select: { x: c.x, y: c.series.map((s) => s.key) },
    chart: { type: c.type, title: c.title, stacked: c.stacked, unit: c.unit }
  }
}
