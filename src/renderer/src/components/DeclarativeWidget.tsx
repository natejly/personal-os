import { useMemo } from 'react'
import { ResponsiveContainer } from 'recharts'
import { AlertCircle } from 'lucide-react'
import type { Widget } from '@shared/types'
import { Chart, DataTable, fmtNum, parseSpec, type Spec } from './ChartBlock'
import { boundChartSource, boundColumns, boundRows, boundStat, fmtDelta } from '../lib/boundWidget'

/** A big number with an optional change under it. */
function StatTile({ widget }: { widget: Widget }): JSX.Element {
  const s = boundStat(widget)
  if (!s) return <div className="dw-empty">No value yet.</div>
  const d = fmtDelta(s.delta)
  return (
    <div className="stat-tile">
      <div className="stat-label">{s.label}</div>
      <div className="stat-value">{fmtNum(s.value, s.unit)}</div>
      {d && <div className={`stat-delta ${s.delta! > 0 ? 'up' : s.delta! < 0 ? 'down' : ''}`}>{d}{s.unit && s.unit !== '$' && s.unit !== '%' ? ` ${s.unit}` : ''}</div>}
    </div>
  )
}

function BoundTable({ widget }: { widget: Widget }): JSX.Element {
  const rows = boundRows(widget)
  const cols = boundColumns(widget)
  if (!rows.length || !cols.length) return <div className="dw-empty">No rows yet.</div>
  return (
    <div className="chart-table">
      <table>
        <thead><tr>{cols.map((c) => <th key={c.key}>{c.label}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c.key}>{typeof r[c.key] === 'object' ? JSON.stringify(r[c.key]) : String(r[c.key] ?? '')}</td>)}</tr>)}</tbody>
      </table>
    </div>
  )
}

/**
 * chart | stat | table, drawn by the app's own components from rows the backend bound and cached. Nothing here is
 * model-written code, so there is no iframe: the chart colours come from the --chart-N tokens like every other chart.
 */
export default function DeclarativeWidget({ widget, height = 260 }: { widget: Widget; height?: number }): JSX.Element {
  const parsed = useMemo<{ spec: Spec } | { error: string } | null>(() => {
    if (widget.kind !== 'chart') return null
    const src = boundChartSource(widget)
    if (!src) return null
    try { return { spec: parseSpec(src) } } catch (e) { return { error: (e as Error).message } }
  }, [widget])

  const err = widget.data_error ? (
    <div className="dw-empty" role="alert" style={{ display: 'flex', gap: 6, alignItems: 'center' }}><AlertCircle size={13} /> {widget.data_error}</div>
  ) : null

  if (widget.kind === 'stat') return <>{err}<StatTile widget={widget} /></>
  if (widget.kind === 'table') return <>{err}<BoundTable widget={widget} /></>
  if (!parsed) return err ?? <div className="dw-empty">No data yet.</div>
  if ('error' in parsed) return <div className="dw-empty">Couldn&apos;t draw this chart: {parsed.error}</div>
  return (
    <>
      {err}
      {parsed.spec.title && <div className="stat-label" style={{ padding: '4px 12px 0' }}>{parsed.spec.title}</div>}
      <div className="chart-canvas" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%"><Chart spec={parsed.spec} /></ResponsiveContainer>
      </div>
      {widget.data && boundRows(widget).length > 0 && <details className="dw-md"><summary className="muted small">Data</summary><DataTable spec={parsed.spec} /></details>}
    </>
  )
}
