import { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCw, AlertCircle, Check } from 'lucide-react'
import { api } from '../lib/api'
import type { ModelPrice, UsageBucket, UsageReport } from '@shared/types'
import ChartBlock from './ChartBlock'

/** Cost / token / frequency charts over the usage log, plus per-model price overrides. */

const RANGES = [7, 30, 90] as const

export const money = (n: number): string =>
  n === 0 ? '$0' : n < 0.01 ? `$${n.toFixed(4)}` : n < 1 ? `$${n.toFixed(3)}` : `$${n.toFixed(2)}`
export const compact = (n: number): string => new Intl.NumberFormat(undefined, { notation: n >= 100000 ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(n)
export const ms = (n: number): string => (n < 1000 ? `${n} ms` : `${(n / 1000).toFixed(1)} s`)
/** "2026-09-29" → "Sep 29" */
export const shortDay = (iso: string): string => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
const hourLabel = (h: number): string => `${((h + 11) % 12) + 1}${h < 12 ? 'am' : 'pm'}`

export const spec = (o: Record<string, unknown>): string => JSON.stringify(o)

const pct = (f: number): string => `${Math.round(f * 100)}%`

export function Tile({ label, value, sub }: { label: string; value: string; sub?: string }): JSX.Element {
  return (
    <div className="usage-tile">
      <span className="usage-tile-label">{label}</span>
      <b className="usage-tile-value">{value}</b>
      {sub && <small className="usage-tile-sub">{sub}</small>}
    </div>
  )
}

function PriceEditor({ report, onSaved }: { report: UsageReport; onSaved: (prices: Record<string, ModelPrice>) => void }): JSX.Element {
  // Every model seen in the log, plus every model the proxy prices.
  const models = useMemo(
    () => Array.from(new Set([...report.by_model.map((m) => m.model), ...Object.keys(report.prices)])).sort(),
    [report]
  )
  const [draft, setDraft] = useState<Record<string, { input: string; output: string }>>({})
  const [saving, setSaving] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [msg, setMsg] = useState('')

  const valueOf = (m: string, side: 'input' | 'output'): string =>
    draft[m]?.[side] ?? (report.prices[m] ? String(report.prices[m][side]) : '')
  const edit = (m: string, side: 'input' | 'output', v: string): void =>
    setDraft((d) => ({ ...d, [m]: { input: valueOf(m, 'input'), output: valueOf(m, 'output'), [side]: v } }))

  const save = async (): Promise<void> => {
    setSaving('saving')
    // Send every model that has a price, so hand-edits and proxy values both persist as overrides.
    const out: Record<string, { input: number; output: number }> = {}
    for (const m of models) {
      const i = Number(valueOf(m, 'input')), o = Number(valueOf(m, 'output'))
      if (Number.isFinite(i) && Number.isFinite(o) && (i > 0 || o > 0)) out[m] = { input: i, output: o }
    }
    try {
      const r = await api.usage.setPrices(out)
      setSaving('saved')
      setMsg(`Re-priced ${r.repriced} call${r.repriced === 1 ? '' : 's'}.`)
      setDraft({})
      onSaved(r.prices)
      setTimeout(() => setSaving('idle'), 2500)
    } catch (e) {
      setSaving('error')
      setMsg((e as Error).message)
    }
  }

  if (models.length === 0) return <p className="muted small">No models used yet.</p>
  return (
    <div className="usage-prices">
      <table>
        <thead><tr><th scope="col">Model</th><th scope="col">Input $/M</th><th scope="col">Output $/M</th><th scope="col" aria-label="Price source" /></tr></thead>
        <tbody>
          {models.map((m) => (
            <tr key={m}>
              <th scope="row" className="mono">{m}</th>
              <td><input type="number" aria-label={`${m} input price, $ per million tokens`} min={0} step="0.01" value={valueOf(m, 'input')} placeholder="—" onChange={(e) => edit(m, 'input', e.target.value)} /></td>
              <td><input type="number" aria-label={`${m} output price, $ per million tokens`} min={0} step="0.01" value={valueOf(m, 'output')} placeholder="—" onChange={(e) => edit(m, 'output', e.target.value)} /></td>
              <td className="usage-price-src">{draft[m] ? 'edited' : report.prices[m]?.source === 'override' ? 'custom' : report.prices[m] ? 'proxy' : 'unpriced'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="test-row">
        <button className="ghost-btn" onClick={() => void save()} disabled={saving === 'saving'}>
          {saving === 'saving' ? 'Saving…' : saving === 'saved' ? <><Check size={14} /> Saved</> : 'Save prices & re-price history'}
        </button>
        {msg && <span className={`test-msg ${saving === 'error' ? 'fail' : 'ok'}`}>{msg}</span>}
      </div>
    </div>
  )
}

export default function UsageView(): JSX.Element {
  const [days, setDays] = useState<number>(30)
  const [report, setReport] = useState<UsageReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async (d: number) => {
    setLoading(true)
    try {
      setReport(await api.usage.report(d))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load(days) }, [days, load])

  const charts = useMemo(() => {
    if (!report) return null
    const daily = report.daily.map((d) => ({ day: shortDay(d.day), cost: Number(d.cost.toFixed(6)), in: d.prompt_tokens, out: d.completion_tokens, calls: d.calls }))
    const models = report.by_model.slice(0, 8).map((m) => ({ model: m.model, cost: Number(m.cost.toFixed(6)), tokens: m.tokens, calls: m.calls }))
    return {
      cost: spec({ type: 'area', title: `Cost per day (last ${report.days} days)`, x: 'day', series: ['cost'], unit: '$', data: daily }),
      tokens: spec({ type: 'bar', title: 'Tokens per day', x: 'day', stacked: true, series: [{ key: 'in', label: 'Input' }, { key: 'out', label: 'Output' }], data: daily }),
      calls: spec({ type: 'bar', title: 'Calls per day', x: 'day', series: [{ key: 'calls', label: 'Model calls' }], data: daily }),
      byModel: spec({ type: 'bar', title: 'Cost by model', x: 'model', series: [{ key: 'cost', label: 'Cost' }], unit: '$', data: models }),
      tokensByModel: spec({ type: 'bar', title: 'Tokens by model', x: 'model', series: [{ key: 'tokens', label: 'Tokens' }], data: models }),
      hourly: spec({ type: 'bar', title: 'When you chat (hour of day)', x: 'hour', series: [{ key: 'calls', label: 'Chat calls' }], data: report.hourly.map((h) => ({ hour: hourLabel(h.hour), calls: h.calls })) }),
      weekday: spec({ type: 'bar', title: 'When you chat (day of week)', x: 'weekday', series: [{ key: 'calls', label: 'Chat calls' }], data: report.weekday }),
      byProject: spec({ type: 'pie', title: 'Tokens by project', x: 'project', series: [{ key: 'tokens', label: 'Tokens' }], data: report.by_project.slice(0, 8).map((p) => ({ project: p.project, tokens: p.tokens })) })
    }
  }, [report])

  if (error) return <div className="chart-err"><AlertCircle size={14} /> Couldn't load usage: {error}</div>
  if (!report || !charts) return <p className="muted small">{loading ? 'Loading usage…' : 'No usage recorded yet.'}</p>

  const t: UsageBucket = report.totals
  const empty = t.calls === 0

  return (
    <div className="usage">
      <div className="usage-head">
        <div className="usage-ranges">
          {RANGES.map((d) => <button key={d} className={days === d ? 'active' : ''} onClick={() => setDays(d)}>{d}d</button>)}
        </div>
        <button className="icon-btn ghost" aria-label="Refresh usage" title="Refresh" onClick={() => void load(days)}><RefreshCw size={13} className={loading ? 'spin' : ''} /></button>
      </div>

      {empty ? (
        <p className="muted small">No model calls in this range. Usage is logged from the first reply after this build.</p>
      ) : (
        <>
          <div className="usage-tiles">
            <Tile label="Spend" value={money(t.cost)} sub={t.unpriced ? `${t.unpriced} call${t.unpriced === 1 ? '' : 's'} unpriced` : `over ${report.days} days`} />
            <Tile label="Tokens" value={compact(t.tokens)} sub={`${compact(t.prompt_tokens)} in · ${compact(t.completion_tokens)} out`} />
            <Tile label="Model calls" value={String(t.calls)} sub={`${t.chat_calls} chat · ${t.learn_calls} auto-learn${t.other_calls ? ` · ${t.other_calls} other` : ''}`} />
            <Tile label="Avg latency" value={ms(t.avg_ms)} sub="per model call" />
            <Tile label="Cache hit rate" value={pct(t.cache_hit_rate ?? 0)} sub={`${compact(t.cached_tokens ?? 0)} input tokens served from the provider cache`} />
            <Tile label="Reasoning tokens" value={compact(t.reasoning_tokens ?? 0)} sub={`${pct(t.reasoning_share ?? 0)} of output tokens`} />
          </div>

          <div className="markdown usage-charts">
            <ChartBlock source={charts.cost} streaming={false} />
            <ChartBlock source={charts.tokens} streaming={false} />
            <ChartBlock source={charts.calls} streaming={false} />
            <ChartBlock source={charts.byModel} streaming={false} />
            <ChartBlock source={charts.tokensByModel} streaming={false} />
            <ChartBlock source={charts.hourly} streaming={false} />
            <ChartBlock source={charts.weekday} streaming={false} />
            {report.by_project.length > 1 && <ChartBlock source={charts.byProject} streaming={false} />}
          </div>
          {report.by_tag?.length > 0 && (
            <>
              <h4 className="usage-sub">By source</h4>
              <table className="usage-table">
                <tbody>
                  {report.by_tag.slice(0, 12).map((t) => (
                    <tr key={t.tag}><td>{t.tag}</td><td>{t.calls} calls</td><td>{t.tokens.toLocaleString()} tokens</td><td>${t.cost.toFixed(4)}</td></tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}

      <h4 className="usage-sub">Prices</h4>
      <p className="muted small">Read from your LiteLLM proxy. Saving re-prices the whole history.</p>
      <PriceEditor report={report} onSaved={(prices) => setReport((r) => (r ? { ...r, prices } : r))} />
    </div>
  )
}
