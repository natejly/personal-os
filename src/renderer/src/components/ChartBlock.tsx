import { useMemo, useState, type ReactNode } from 'react'
import {
  ResponsiveContainer, ComposedChart, BarChart, Bar, Line, Area, PieChart, Pie, Cell, ScatterChart, Scatter,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, Brush
} from 'recharts'
import { parseJsonLoose } from '../lib/chartRepair'
import { AlertCircle, BarChart3, Check, Code2, Copy, Table2 } from 'lucide-react'
import { applyTransforms, isIsoDateColumn, fmtIsoDate, BRUSH_ABOVE, MAX_ROWS } from '../lib/chartTransforms'

/**
 * Renders a ```chart fenced block: a compact JSON spec the model writes (see RENDER_HINT in the backend).
 * Accepts the documented shape plus a couple of common variants (Chart.js-style labels/datasets, key→value maps).
 */

export type Row = Record<string, unknown>
export type ChartType = 'bar' | 'line' | 'area' | 'pie' | 'scatter'
export interface Series { key: string; label: string; type: ChartType }
export interface Spec {
  type: ChartType
  title: string
  x: string
  series: Series[]
  data: Row[]
  stacked: boolean
  xLabel: string
  yLabel: string
  unit: string
  /**
   * A continuous x axis, scaled by value rather than one slot per row. Interactive blocks set this for a
   * swept range so 200 sample points do not become 200 category ticks; ```chart blocks leave it unset.
   */
  xType?: 'category' | 'number'
}

export const TYPES: ChartType[] = ['bar', 'line', 'area', 'pie', 'scatter']
export const COLORS = 8 // --chart-1 … --chart-8 in styles.css

export function num(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v === 'string' && v.trim() !== '' && !Number.isNaN(Number(v))) return Number(v)
  return null
}

export function parseSpec(source: string): Spec {
  const raw = parseJsonLoose(source) as Record<string, unknown>
  if (!raw || typeof raw !== 'object') throw new Error('Chart spec must be a JSON object')
  let data: Row[] = []
  let x = typeof raw.x === 'string' ? raw.x : ''
  let seriesIn: unknown = raw.series ?? raw.y

  if (Array.isArray(raw.labels) && Array.isArray(raw.datasets)) {
    // Chart.js shape: { labels: [...], datasets: [{ label, data: [...] }] }
    const ds = raw.datasets as { label?: string; data?: unknown[] }[]
    const keys = ds.map((d, i) => d.label || `Series ${i + 1}`)
    data = (raw.labels as unknown[]).map((l, i) => ({ label: String(l), ...Object.fromEntries(ds.map((d, j) => [keys[j], d.data?.[i]])) }))
    x = 'label'
    seriesIn = keys
  } else if (Array.isArray(raw.data)) {
    data = (raw.data as unknown[]).filter((r): r is Row => !!r && typeof r === 'object' && !Array.isArray(r))
    if (data.length === 0 && (raw.data as unknown[]).every((r) => Array.isArray(r))) {
      // [[x, y], ...]
      data = (raw.data as unknown[][]).map((r) => ({ x: r[0], y: r[1] }))
      x = 'x'
      seriesIn = ['y']
    }
  } else if (raw.data && typeof raw.data === 'object') {
    // { data: { A: 1, B: 2 } }
    data = Object.entries(raw.data as Record<string, unknown>).map(([k, v]) => ({ name: k, value: v }))
    x = 'name'
    seriesIn = ['value']
  }
  if (data.length === 0) throw new Error('Chart has no data rows')
  data = data.slice(0, MAX_ROWS)
  // optional spec.transforms (sort | limit | filter | group)
  if (raw.transforms !== undefined) data = applyTransforms(data, raw.transforms)
  if (data.length === 0) throw new Error('The transforms leave no rows')

  const keys = Array.from(new Set(data.flatMap((r) => Object.keys(r))))
  if (!x || !keys.includes(x)) x = keys.find((k) => data.some((r) => typeof r[k] === 'string' && num(r[k]) === null)) ?? keys[0]

  const type: ChartType = TYPES.includes(raw.type as ChartType) ? (raw.type as ChartType) : 'bar'
  let series: Series[] = []
  const listed = typeof seriesIn === 'string' ? [seriesIn] : Array.isArray(seriesIn) ? seriesIn : []
  for (const s of listed) {
    if (typeof s === 'string') series.push({ key: s, label: s, type })
    else if (s && typeof s === 'object' && typeof (s as Row).key === 'string') {
      const o = s as Row
      series.push({ key: o.key as string, label: typeof o.label === 'string' ? o.label : (o.key as string), type: TYPES.includes(o.type as ChartType) ? (o.type as ChartType) : type })
    }
  }
  series = series.filter((s) => keys.includes(s.key) && s.key !== x)
  if (series.length === 0) series = keys.filter((k) => k !== x && data.some((r) => num(r[k]) !== null)).map((k) => ({ key: k, label: k, type }))
  if (series.length === 0) throw new Error('No numeric series found in data')
  series = series.slice(0, COLORS)

  // coerce numeric strings so axes scale properly
  data = data.map((r) => ({ ...r, ...Object.fromEntries(series.map((s) => [s.key, num(r[s.key])])) }))

  return {
    type, x, series, data,
    title: typeof raw.title === 'string' ? raw.title : '',
    stacked: raw.stacked === true,
    xLabel: typeof raw.xLabel === 'string' ? raw.xLabel : '',
    yLabel: typeof raw.yLabel === 'string' ? raw.yLabel : '',
    unit: typeof raw.unit === 'string' ? raw.unit : ''
  }
}

export const fmtNum = (v: unknown, unit = ''): string => {
  const n = num(v)
  if (n === null) return String(v ?? '')
  const a = Math.abs(n)
  // Below 1, two significant digits: sub-cent ticks would all round to "0" with fixed decimals.
  const digits = a > 0 && a < 1 ? { maximumSignificantDigits: 2 } : { maximumFractionDigits: a >= 100 ? 0 : 2 }
  const s = new Intl.NumberFormat(undefined, { ...digits, notation: a >= 100000 ? 'compact' : 'standard' }).format(n)
  if (unit === '$' || unit === '€' || unit === '£') return unit + s
  if (unit === '%') return s + '%'
  return unit ? `${s} ${unit}` : s
}

export const color = (i: number): string => `var(--chart-${(i % COLORS) + 1})`
const tick = { fill: 'var(--chart-ink)', fontSize: 11 }
const tooltipStyle = { background: 'var(--bg-elev)', border: '1px solid var(--border-strong)', borderRadius: 8, fontSize: 12, color: 'var(--text)', boxShadow: '0 4px 16px rgba(0,0,0,0.25)' }

export function Chart({ spec }: { spec: Spec }): JSX.Element {
  const { type, x, series, data, stacked, unit } = spec
  // keep series in the order the spec lists them (Recharts 3 sorts legends/tooltips alphabetically by default)
  const legend = series.length > 1 ? <Legend wrapperStyle={{ fontSize: 12, paddingTop: 8 }} iconType="circle" iconSize={8} itemSorter={null} /> : null
  const tooltip = <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'var(--hover)', stroke: 'var(--chart-axis)' }} formatter={(v: unknown) => fmtNum(v, unit)} itemSorter={() => 0} />
  const numericX = spec.xType === 'number'
  const dateX = !numericX && isIsoDateColumn(data, x)
  const xAxis = (
    <XAxis dataKey={x} tick={tick} axisLine={{ stroke: 'var(--chart-axis)' }} tickLine={false}
      type={numericX ? 'number' : 'category'}
      domain={numericX ? ['dataMin', 'dataMax'] : undefined}
      tickFormatter={numericX ? (v: unknown) => fmtNum(v) : dateX ? fmtIsoDate : undefined}
      label={spec.xLabel ? { value: spec.xLabel, position: 'insideBottom', offset: -2, fill: 'var(--chart-ink)', fontSize: 11 } : undefined} />
  )
  // bars need a zero baseline; lines/areas read better zoomed to the data range
  const yDomain: [string | number, string | number] = type === 'line' && !stacked ? ['auto', 'auto'] : [0, 'auto']
  // The axis needs extra width when it carries a label, or the rotated text sits on top of the ticks.
  const yAxis = <YAxis tick={tick} axisLine={false} tickLine={false} width={spec.yLabel ? 66 : 48} domain={yDomain} tickFormatter={(v: unknown) => fmtNum(v, unit)} label={spec.yLabel ? { value: spec.yLabel, angle: -90, position: 'insideLeft', fill: 'var(--chart-ink)', fontSize: 11 } : undefined} />
  const grid = <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
  const margin = { top: 8, right: 12, bottom: spec.xLabel ? 16 : 0, left: 0 }

  if (type === 'pie') {
    const s = series[0]
    return (
      <PieChart margin={margin}>
        {tooltip}
        <Legend wrapperStyle={{ fontSize: 12 }} iconType="circle" iconSize={8} itemSorter={null} />
        <Pie data={data} dataKey={s.key} nameKey={x} innerRadius="52%" outerRadius="82%" paddingAngle={1.5} stroke="var(--bg-elev)" strokeWidth={2}>
          {data.map((_, i) => <Cell key={i} fill={color(i)} />)}
        </Pie>
      </PieChart>
    )
  }
  if (type === 'scatter') {
    return (
      <ScatterChart margin={margin}>
        {grid}
        <XAxis dataKey={x} type="number" name={spec.xLabel || x} tick={tick} axisLine={{ stroke: 'var(--chart-axis)' }} tickLine={false} tickFormatter={(v: unknown) => fmtNum(v)} />
        <YAxis dataKey={series[0].key} type="number" name={spec.yLabel || series[0].label} tick={tick} axisLine={false} tickLine={false} width={48} tickFormatter={(v: unknown) => fmtNum(v, unit)} />
        <Tooltip contentStyle={tooltipStyle} cursor={{ strokeDasharray: '3 3', stroke: 'var(--chart-axis)' }} formatter={(v: unknown) => fmtNum(v, unit)} itemSorter={() => 0} />
        {legend}
        {series.map((s, i) => <Scatter key={s.key} name={s.label} data={data} dataKey={s.key} fill={color(i)} stroke="var(--bg-elev)" strokeWidth={1} />)}
      </ScatterChart>
    )
  }
  const mixed = series.some((s) => s.type !== type)
  const stackId = stacked ? 'stack' : undefined
  const el = (s: Series, i: number): JSX.Element => {
    const t = mixed ? s.type : type
    if (t === 'line') return <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={color(i)} strokeWidth={2} dot={data.length <= 30 ? { r: 3, strokeWidth: 0, fill: color(i) } : false} activeDot={{ r: 5, stroke: 'var(--bg-elev)', strokeWidth: 2 }} isAnimationActive={false} />
    if (t === 'area') return <Area key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={color(i)} fill={color(i)} fillOpacity={0.18} strokeWidth={2} stackId={stackId} dot={false} activeDot={{ r: 5, stroke: 'var(--bg-elev)', strokeWidth: 2 }} isAnimationActive={false} />
    return <Bar key={s.key} dataKey={s.key} name={s.label} fill={color(i)} stackId={stackId} maxBarSize={44} radius={stacked ? 0 : [3, 3, 0, 0]} stroke="var(--bg-elev)" strokeWidth={stacked ? 1 : 0} isAnimationActive={false} />
  }
  const Wrapper = mixed || type !== 'bar' ? ComposedChart : BarChart
  return (
    <Wrapper data={data} margin={margin} barCategoryGap="22%" barGap={2}>
      {grid}
      {xAxis}
      {yAxis}
      {tooltip}
      {legend}
      {series.map(el)}
      {data.length > BRUSH_ABOVE && <Brush dataKey={x} height={22} stroke="var(--chart-axis)" fill="var(--bg-elev)" tickFormatter={dateX ? fmtIsoDate : undefined} />}
    </Wrapper>
  )
}

export function DataTable({ spec }: { spec: Spec }): JSX.Element {
  return (
    <div className="chart-table">
      <table>
        <thead><tr><th>{spec.xLabel || spec.x}</th>{spec.series.map((s) => <th key={s.key}>{s.label}</th>)}</tr></thead>
        <tbody>{spec.data.map((r, i) => <tr key={i}><td>{String(r[spec.x] ?? '')}</td>{spec.series.map((s) => <td key={s.key} className="num">{fmtNum(r[s.key], spec.unit)}</td>)}</tr>)}</tbody>
      </table>
    </div>
  )
}

export type ChartView = 'chart' | 'table' | 'source'

/** The chart/table/source/copy toolbar both chart blocks share; `children` are the block's own actions, before Copy. */
export function ChartTools({ view, setView, source, chartIcon, children }: { view: ChartView; setView: (v: ChartView) => void; source: string; chartIcon: ReactNode; children?: ReactNode }): JSX.Element {
  const [copied, setCopied] = useState(false)
  const copy = (): void => { void navigator.clipboard.writeText(source); setCopied(true); setTimeout(() => setCopied(false), 1200) }
  return (
    <div className="chart-tools">
      <button className={`icon-btn ghost ${view === 'chart' ? 'on' : ''}`} title="Chart" onClick={() => setView('chart')}>{chartIcon}</button>
      <button className={`icon-btn ghost ${view === 'table' ? 'on' : ''}`} title="Data table" onClick={() => setView('table')}><Table2 size={13} /></button>
      <button className={`icon-btn ghost ${view === 'source' ? 'on' : ''}`} title="Spec source" onClick={() => setView('source')}><Code2 size={13} /></button>
      {children}
      <button className="icon-btn ghost" title="Copy spec" onClick={copy}>{copied ? <Check size={13} /> : <Copy size={13} />}</button>
    </div>
  )
}

export default function ChartBlock({ source, streaming }: { source: string; streaming: boolean }): JSX.Element {
  const [view, setView] = useState<ChartView>('chart')
  const parsed = useMemo<{ spec: Spec } | { error: string }>(() => {
    try { return { spec: parseSpec(source) } } catch (e) { return { error: (e as Error).message } }
  }, [source])

  if ('error' in parsed) {
    if (streaming) return <div className="chart-block placeholder"><BarChart3 size={14} /> Building chart…</div>
    return (
      <div className="chart-block error">
        <div className="chart-err"><AlertCircle size={14} /> Couldn't render chart: {parsed.error}</div>
        <pre>{source}</pre>
      </div>
    )
  }
  const { spec } = parsed
  return (
    <figure className="chart-block">
      <div className="code-head">
        <span>{spec.title || `${spec.type} chart`}</span>
        <ChartTools view={view} setView={setView} source={source} chartIcon={<BarChart3 size={13} />} />
      </div>
      {view === 'chart' && (
        <div className="chart-canvas" style={{ height: spec.type === 'pie' ? 260 : 280 }}>
          <ResponsiveContainer width="100%" height="100%"><Chart spec={spec} /></ResponsiveContainer>
        </div>
      )}
      {view === 'table' && <DataTable spec={spec} />}
      {view === 'source' && <pre className="chart-source">{source}</pre>}
    </figure>
  )
}
