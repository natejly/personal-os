import { useMemo, useState } from 'react'
import { ResponsiveContainer } from 'recharts'
import { SlidersHorizontal, Table2, Code2, Copy, Check, AlertCircle, RotateCcw } from 'lucide-react'
import { Chart, DataTable, fmtNum, TYPES, type ChartType, type Row, type Series, type Spec } from './ChartBlock'
import { tryCompile, type Compiled, type Scope, type Value } from '../lib/expr'

/**
 * Renders an ```interactive fenced block: a chart whose series are formulas over named controls, so
 * dragging a slider redraws it locally with no round trip to the model (see RENDER_HINT in the backend).
 *
 * Formulas go through lib/expr, never `eval` — the spec is model-authored and the model may have read
 * an untrusted page. Everything here is bounded too: a fixed control/series/point ceiling means a
 * hostile or sloppy spec costs a predictable amount of work per frame.
 */

const MAX_CONTROLS = 12
const MAX_SERIES = 8
const MAX_READOUTS = 6
const MAX_POINTS = 400
const MAX_ROWS = 500
const ID_RE = /^[A-Za-z_][A-Za-z0-9_]*$/

type ControlKind = 'slider' | 'number' | 'select' | 'toggle'

interface Control {
  id: string
  label: string
  kind: ControlKind
  min: number
  max: number
  step: number
  unit: string
  options: string[]
  initial: Value
}

interface SeriesSpec {
  key: string
  label: string
  type: ChartType
  /** null in data mode, where the series reads a column straight off the row. */
  expr: Compiled | null
}

interface Readout {
  label: string
  expr: Compiled
  unit: string
  digits: number | null
}

type XSpec =
  | { mode: 'sweep'; id: string; from: Compiled; to: Compiled; steps: Compiled }
  | { mode: 'values'; id: string; values: Value[] }
  | { mode: 'data'; id: string; rows: Row[] }

interface Parsed {
  title: string
  type: ChartType
  stacked: boolean
  xLabel: string
  yLabel: string
  unit: string
  controls: Control[]
  x: XSpec
  series: SeriesSpec[]
  readouts: Readout[]
  /** Changes whenever the control set does, so live values reset instead of sticking to a stale spec. */
  signature: string
}

// ---------------------------------------------------------------- parsing

const str = (v: unknown, fallback = ''): string => (typeof v === 'string' ? v : fallback)
const bool = (v: unknown): boolean => v === true
const fin = (v: unknown): number | null => {
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN
  return Number.isFinite(n) ? n : null
}

/** A slider with no step gets a round one: ~100 stops across its range. */
function niceStep(min: number, max: number): number {
  const span = Math.abs(max - min)
  if (!Number.isFinite(span) || span === 0) return 1
  const raw = span / 100
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  const norm = raw / mag
  return (norm >= 5 ? 5 : norm >= 2 ? 2 : 1) * mag
}

/** Compile one formula, tagging the failure with which field it came from. */
function expr(source: unknown, where: string): Compiled {
  if (typeof source !== 'string' || !source.trim()) throw new Error(`${where} needs a formula string`)
  const r = tryCompile(source)
  if (!r.ok) throw new Error(`${where}: ${r.error}`)
  return r.expr
}

/** A number, or a formula over the controls when written as a string. */
function numOrExpr(v: unknown, fallback: number, where: string): Compiled {
  if (typeof v === 'number' && Number.isFinite(v)) return expr(String(v), where)
  if (typeof v === 'string' && v.trim()) return expr(v, where)
  return expr(String(fallback), where)
}

function parseControl(raw: unknown, i: number): Control {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error(`controls[${i}] must be an object`)
  const o = raw as Record<string, unknown>
  const id = str(o.id) || str(o.name) || str(o.key)
  if (!id) throw new Error(`controls[${i}] needs an "id"`)
  if (!ID_RE.test(id)) throw new Error(`Control id ${JSON.stringify(id)} must be a plain name (letters, digits, underscore; not starting with a digit) so formulas can reference it`)

  const options = Array.isArray(o.options)
    ? (o.options as unknown[]).map((v) => (v && typeof v === 'object' ? str((v as Record<string, unknown>).value) : String(v))).filter((s) => s !== '').slice(0, 24)
    : []
  const given = o.value !== undefined ? o.value : o.default
  const declared = str(o.type) || str(o.kind)
  const kind: ControlKind =
    declared === 'slider' || declared === 'range' ? 'slider'
      : declared === 'number' ? 'number'
        : declared === 'select' || declared === 'choice' ? 'select'
          : declared === 'toggle' || declared === 'checkbox' || declared === 'boolean' ? 'toggle'
            : options.length > 0 ? 'select'
              : typeof given === 'boolean' ? 'toggle'
                : 'slider'

  if (kind === 'select') {
    if (options.length === 0) throw new Error(`Control ${JSON.stringify(id)} is a select but has no "options"`)
    const initial = typeof given === 'string' && options.includes(given) ? given : options[0]
    return { id, label: str(o.label, id), kind, min: 0, max: 0, step: 0, unit: str(o.unit), options, initial }
  }
  if (kind === 'toggle') {
    return { id, label: str(o.label, id), kind, min: 0, max: 1, step: 1, unit: str(o.unit), options: [], initial: given === true || given === 1 ? 1 : 0 }
  }

  const value = fin(given)
  let min = fin(o.min)
  let max = fin(o.max)
  if (min === null) min = Math.min(0, value ?? 0)
  if (max === null) max = Math.max(min + 1, (value ?? 1) * 2 || 1)
  if (max <= min) max = min + 1
  const step = fin(o.step) ?? niceStep(min, max)
  const initial = Math.min(Math.max(value ?? min, min), max)
  return { id, label: str(o.label, id), kind, min, max, step: step > 0 ? step : niceStep(min, max), unit: str(o.unit), options: [], initial }
}

/** Reject a formula that reads a name nothing will supply — a model typo, reported rather than drawn as NaN. */
function checkVars(e: Compiled, allowed: Set<string>, where: string): void {
  const unknown = e.vars.filter((v) => !allowed.has(v))
  if (unknown.length > 0) {
    throw new Error(`${where} refers to ${unknown.map((u) => JSON.stringify(u)).join(', ')}, which ${unknown.length === 1 ? 'is not' : 'are not'} defined. Available: ${[...allowed].sort().join(', ')}`)
  }
}

export function parseInteractive(source: string): Parsed {
  const raw = JSON.parse(source) as Record<string, unknown>
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('Interactive spec must be a JSON object')

  const rawControls = Array.isArray(raw.controls) ? raw.controls : Array.isArray(raw.inputs) ? raw.inputs : []
  if (rawControls.length > MAX_CONTROLS) throw new Error(`Too many controls (limit ${MAX_CONTROLS})`)
  const controls = rawControls.map(parseControl)
  const ids = new Set<string>()
  for (const c of controls) {
    if (ids.has(c.id)) throw new Error(`Duplicate control id ${JSON.stringify(c.id)}`)
    ids.add(c.id)
  }

  const controlScope = new Set(ids)
  const type: ChartType = TYPES.includes(raw.type as ChartType) ? (raw.type as ChartType) : 'line'

  // ---- x axis: a swept numeric range, an explicit list, or a column of supplied rows
  let x: XSpec
  const rawX = raw.x
  const rows = Array.isArray(raw.data)
    ? (raw.data as unknown[]).filter((r): r is Row => !!r && typeof r === 'object' && !Array.isArray(r)).slice(0, MAX_ROWS)
    : []
  const xo = rawX && typeof rawX === 'object' && !Array.isArray(rawX) ? (rawX as Record<string, unknown>) : null
  const xId = (xo ? str(xo.id) || str(xo.key) || str(xo.name) : str(rawX)) || 'x'
  if (!ID_RE.test(xId) && rows.length === 0) throw new Error(`x id ${JSON.stringify(xId)} must be a plain name so formulas can reference it`)

  if (rows.length > 0) {
    const keys = new Set(rows.flatMap((r) => Object.keys(r)))
    x = { mode: 'data', id: keys.has(xId) ? xId : ([...keys][0] ?? 'x'), rows }
  } else if (xo && Array.isArray(xo.values)) {
    const values = (xo.values as unknown[]).slice(0, MAX_ROWS).map((v) => (typeof v === 'number' ? v : String(v)))
    if (values.length === 0) throw new Error('x.values is empty')
    x = { mode: 'values', id: xId, values }
  } else {
    const from = numOrExpr(xo?.from ?? xo?.min, 0, 'x.from')
    const to = numOrExpr(xo?.to ?? xo?.max, 10, 'x.to')
    const steps = numOrExpr(xo?.steps ?? xo?.points, 120, 'x.steps')
    for (const [e, name] of [[from, 'x.from'], [to, 'x.to'], [steps, 'x.steps']] as const) checkVars(e, controlScope, name)
    x = { mode: 'sweep', id: xId, from, to, steps }
  }

  // ---- what a series formula may read
  const pointScope = new Set([...ids, x.id, 'x', 'index', 'n'])
  if (x.mode === 'data') for (const r of x.rows) for (const k of Object.keys(r)) pointScope.add(k)

  const rawSeries = Array.isArray(raw.series) ? raw.series : Array.isArray(raw.y) ? raw.y : []
  if (rawSeries.length === 0) throw new Error('Interactive spec needs at least one entry in "series"')
  if (rawSeries.length > MAX_SERIES) throw new Error(`Too many series (limit ${MAX_SERIES})`)
  const series: SeriesSpec[] = rawSeries.map((s, i) => {
    if (typeof s === 'string') {
      // A bare string is a formula in computed mode, or a column name in data mode.
      if (x.mode === 'data' && pointScope.has(s)) return { key: s, label: s, type, expr: null }
      const e = expr(s, `series[${i}]`)
      checkVars(e, pointScope, `series[${i}]`)
      return { key: `y${i + 1}`, label: s, type, expr: e }
    }
    if (!s || typeof s !== 'object') throw new Error(`series[${i}] must be a string or an object`)
    const o = s as Record<string, unknown>
    const key = str(o.key) || str(o.id) || `y${i + 1}`
    const t: ChartType = TYPES.includes(o.type as ChartType) ? (o.type as ChartType) : type
    const formula = o.expr ?? o.formula ?? o.f
    if (formula === undefined) {
      if (x.mode === 'data' && pointScope.has(key)) return { key, label: str(o.label, key), type: t, expr: null }
      throw new Error(`series[${i}] needs an "expr" formula`)
    }
    const e = expr(formula, `series[${i}] ("${key}")`)
    checkVars(e, pointScope, `series[${i}] ("${key}")`)
    return { key, label: str(o.label, key), type: t, expr: e }
  })
  const seen = new Set<string>()
  for (const s of series) {
    if (seen.has(s.key)) throw new Error(`Duplicate series key ${JSON.stringify(s.key)}`)
    seen.add(s.key)
  }

  // ---- readouts: scalars over the controls plus per-series aggregates
  const aggregates = ['last', 'first', 'min', 'max', 'sum', 'mean']
  const readoutScope = new Set([...ids, 'n', ...series.flatMap((s) => aggregates.map((a) => `${s.key}_${a}`))])
  const rawReadouts = Array.isArray(raw.readouts) ? raw.readouts : Array.isArray(raw.stats) ? raw.stats : []
  if (rawReadouts.length > MAX_READOUTS) throw new Error(`Too many readouts (limit ${MAX_READOUTS})`)
  const readouts: Readout[] = rawReadouts.map((r, i) => {
    if (!r || typeof r !== 'object') throw new Error(`readouts[${i}] must be an object`)
    const o = r as Record<string, unknown>
    const e = expr(o.expr ?? o.formula ?? o.value, `readouts[${i}]`)
    checkVars(e, readoutScope, `readouts[${i}]`)
    return { label: str(o.label, `Value ${i + 1}`), expr: e, unit: str(o.unit, str(raw.unit)), digits: fin(o.digits) }
  })

  return {
    title: str(raw.title),
    type,
    stacked: bool(raw.stacked),
    xLabel: str(raw.xLabel, xo ? str(xo.label) : ''),
    yLabel: str(raw.yLabel),
    unit: str(raw.unit),
    controls, x, series, readouts,
    signature: JSON.stringify(controls.map((c) => [c.id, c.kind, c.initial, c.min, c.max, c.step, c.options]))
  }
}

// ---------------------------------------------------------------- evaluation

/** Trim the float noise a swept range accumulates (0.30000000000000004) without touching real precision. */
const tidy = (n: number): number => (Number.isFinite(n) ? Number(n.toPrecision(12)) : n)

/** Row fields a formula may read: primitives only, so nothing structural leaks into a scope. */
function rowScope(row: Row): Scope {
  const out: Scope = {}
  for (const [k, v] of Object.entries(row)) {
    if (typeof v === 'number' || typeof v === 'string') out[k] = v
    else if (typeof v === 'boolean') out[k] = v ? 1 : 0
  }
  return out
}

interface Computed { rows: Row[]; aggregates: Scope; xType: 'number' | 'category' }

function computeRows(spec: Parsed, values: Scope): Computed {
  const base: Scope = Object.create(null)
  for (const c of spec.controls) base[c.id] = values[c.id] ?? c.initial

  let xs: Value[]
  let rows0: Row[] | null = null
  if (spec.x.mode === 'sweep') {
    const from = spec.x.from.number(base)
    const to = spec.x.to.number(base)
    const rawSteps = spec.x.steps.number(base)
    if (from === null || to === null) throw new Error('x.from and x.to must evaluate to finite numbers')
    const n = Math.min(Math.max(Math.round(rawSteps ?? 120), 2), MAX_POINTS)
    const dx = n > 1 ? (to - from) / (n - 1) : 0
    xs = Array.from({ length: n }, (_, i) => tidy(from + i * dx))
  } else if (spec.x.mode === 'values') {
    xs = spec.x.values
  } else {
    rows0 = spec.x.rows
    xs = rows0.map((r) => {
      const v = r[spec.x.id]
      return typeof v === 'number' || typeof v === 'string' ? v : ''
    })
  }

  const n = xs.length
  const rows: Row[] = []
  for (let i = 0; i < n; i++) {
    const scope: Scope = Object.create(null)
    if (rows0) Object.assign(scope, rowScope(rows0[i]))
    Object.assign(scope, base)
    scope[spec.x.id] = xs[i]
    scope.x = xs[i]
    scope.index = i
    scope.n = n
    const row: Row = { [spec.x.id]: xs[i] }
    for (const s of spec.series) {
      if (s.expr) row[s.key] = s.expr.number(scope)
      else {
        const v = rows0 ? rows0[i][s.key] : null
        const num = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN
        row[s.key] = Number.isFinite(num) ? num : null
      }
    }
    rows.push(row)
  }

  // Aggregates the readouts can name: balance_last, balance_max, …
  const aggregates: Scope = Object.create(null)
  Object.assign(aggregates, base)
  aggregates.n = n
  for (const s of spec.series) {
    const vals = rows.map((r) => r[s.key]).filter((v): v is number => typeof v === 'number')
    const sum = vals.reduce((a, b) => a + b, 0)
    aggregates[`${s.key}_first`] = vals.length ? vals[0] : NaN
    aggregates[`${s.key}_last`] = vals.length ? vals[vals.length - 1] : NaN
    aggregates[`${s.key}_min`] = vals.length ? Math.min(...vals) : NaN
    aggregates[`${s.key}_max`] = vals.length ? Math.max(...vals) : NaN
    aggregates[`${s.key}_sum`] = sum
    aggregates[`${s.key}_mean`] = vals.length ? sum / vals.length : NaN
  }
  return { rows, aggregates, xType: spec.x.mode === 'sweep' ? 'number' : 'category' }
}

// ---------------------------------------------------------------- controls UI

const decimals = (step: number): number => {
  if (!Number.isFinite(step) || step <= 0) return 0
  const s = String(step)
  const dot = s.indexOf('.')
  return dot < 0 ? 0 : Math.min(s.length - dot - 1, 6)
}

function ControlRow({ c, value, onChange }: { c: Control; value: Value; onChange: (v: Value) => void }): JSX.Element {
  if (c.kind === 'select') {
    return (
      <label className="iv-control">
        <span className="iv-label">{c.label}</span>
        <select value={String(value)} onChange={(e) => onChange(e.target.value)}>
          {c.options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </label>
    )
  }
  if (c.kind === 'toggle') {
    return (
      <label className="iv-control toggle">
        <input type="checkbox" checked={value === 1 || value === '1'} onChange={(e) => onChange(e.target.checked ? 1 : 0)} />
        <span className="iv-label">{c.label}</span>
      </label>
    )
  }
  const n = typeof value === 'number' ? value : Number(value)
  const shown = Number.isFinite(n) ? n.toFixed(decimals(c.step)) : ''
  if (c.kind === 'number') {
    return (
      <label className="iv-control">
        <span className="iv-label">{c.label}</span>
        <input type="number" value={Number.isFinite(n) ? n : ''} min={c.min} max={c.max} step={c.step}
          onChange={(e) => { const v = Number(e.target.value); if (Number.isFinite(v)) onChange(v) }} />
        {c.unit && <span className="iv-unit">{c.unit}</span>}
      </label>
    )
  }
  return (
    <label className="iv-control slider">
      <span className="iv-label">{c.label}</span>
      <input type="range" min={c.min} max={c.max} step={c.step} value={Number.isFinite(n) ? n : c.min}
        onChange={(e) => onChange(Number(e.target.value))} />
      <output className="iv-value">{c.unit === '$' || c.unit === '€' || c.unit === '£' ? c.unit + shown : c.unit === '%' ? shown + '%' : c.unit ? `${shown} ${c.unit}` : shown}</output>
    </label>
  )
}

// ---------------------------------------------------------------- block

export default function InteractiveBlock({ source, streaming }: { source: string; streaming: boolean }): JSX.Element {
  const [view, setView] = useState<'chart' | 'table' | 'source'>('chart')
  const [copied, setCopied] = useState(false)
  const parsed = useMemo<{ spec: Parsed } | { error: string }>(() => {
    try { return { spec: parseInteractive(source) } } catch (e) { return { error: (e as Error).message } }
  }, [source])

  const signature = 'spec' in parsed ? parsed.spec.signature : ''
  const initial = useMemo<Scope>(() => {
    const out: Scope = {}
    if ('spec' in parsed) for (const c of parsed.spec.controls) out[c.id] = c.initial
    return out
  }, [signature]) // eslint-disable-line react-hooks/exhaustive-deps
  // Keyed by signature so a spec still streaming in — or edited — starts from its own defaults
  // instead of inheriting values for controls that no longer exist.
  const [live, setLive] = useState<{ sig: string; values: Scope }>({ sig: signature, values: initial })
  const values = live.sig === signature ? live.values : initial
  const setValue = (id: string, v: Value): void => setLive({ sig: signature, values: { ...values, [id]: v } })
  const dirty = 'spec' in parsed && parsed.spec.controls.some((c) => values[c.id] !== c.initial)

  const computed = useMemo<{ out: Computed } | { error: string }>(() => {
    if (!('spec' in parsed)) return { error: '' }
    try { return { out: computeRows(parsed.spec, values) } } catch (e) { return { error: (e as Error).message } }
  }, [parsed, values])

  if ('error' in parsed) {
    if (streaming) return <div className="chart-block placeholder"><SlidersHorizontal size={14} /> Building interactive chart…</div>
    return (
      <div className="chart-block error">
        <div className="chart-err"><AlertCircle size={14} /> Couldn't render interactive chart: {parsed.error}</div>
        <pre>{source}</pre>
      </div>
    )
  }

  const spec = parsed.spec
  const copy = (): void => { void navigator.clipboard.writeText(source); setCopied(true); setTimeout(() => setCopied(false), 1200) }
  const chartSpec: Spec | null = 'out' in computed
    ? {
      type: spec.type, title: spec.title, x: spec.x.id, data: computed.out.rows, stacked: spec.stacked,
      xLabel: spec.xLabel, yLabel: spec.yLabel, unit: spec.unit, xType: computed.out.xType,
      series: spec.series.map<Series>((s) => ({ key: s.key, label: s.label, type: s.type }))
    }
    : null

  return (
    <figure className="chart-block interactive">
      <div className="code-head">
        <span>{spec.title || 'interactive chart'}</span>
        <div className="chart-tools">
          {dirty && (
            <button className="icon-btn ghost" title="Reset controls" onClick={() => setLive({ sig: signature, values: initial })}><RotateCcw size={13} /></button>
          )}
          <button className={`icon-btn ghost ${view === 'chart' ? 'on' : ''}`} title="Chart" onClick={() => setView('chart')}><SlidersHorizontal size={13} /></button>
          <button className={`icon-btn ghost ${view === 'table' ? 'on' : ''}`} title="Data table" onClick={() => setView('table')}><Table2 size={13} /></button>
          <button className={`icon-btn ghost ${view === 'source' ? 'on' : ''}`} title="Spec source" onClick={() => setView('source')}><Code2 size={13} /></button>
          <button className="icon-btn ghost" title="Copy spec" onClick={copy}>{copied ? <Check size={13} /> : <Copy size={13} />}</button>
        </div>
      </div>

      {view !== 'source' && spec.controls.length > 0 && (
        <div className="iv-controls">
          {spec.controls.map((c) => (
            <ControlRow key={c.id} c={c} value={values[c.id] ?? c.initial} onChange={(v) => setValue(c.id, v)} />
          ))}
        </div>
      )}

      {'error' in computed && computed.error && <div className="chart-err"><AlertCircle size={14} /> {computed.error}</div>}

      {view === 'chart' && chartSpec && (
        <div className="chart-canvas" style={{ height: spec.type === 'pie' ? 260 : 280 }}>
          <ResponsiveContainer width="100%" height="100%"><Chart spec={chartSpec} /></ResponsiveContainer>
        </div>
      )}
      {view === 'table' && chartSpec && <DataTable spec={chartSpec} />}
      {view === 'source' && <pre className="chart-source">{source}</pre>}

      {view !== 'source' && spec.readouts.length > 0 && 'out' in computed && (
        <div className="iv-readouts">
          {spec.readouts.map((r, i) => {
            const v = r.expr.number(computed.out.aggregates)
            const text = v === null ? '—' : r.digits !== null ? `${v.toFixed(Math.min(Math.max(r.digits, 0), 6))}${r.unit === '%' ? '%' : r.unit ? ` ${r.unit}` : ''}` : fmtNum(v, r.unit)
            return (
              <div className="iv-readout" key={i}>
                <span className="iv-readout-label">{r.label}</span>
                <span className="iv-readout-value">{text}</span>
              </div>
            )
          })}
        </div>
      )}
    </figure>
  )
}
