import { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshCw, AlertCircle, Check } from 'lucide-react'
import { api } from '../lib/api'
import type { ModelPrice, UsageBucket, UsagePeriod, UsageReport } from '@shared/types'
import ChartBlock from './ChartBlock'
import { costNote, costText, money, overridesToSave, shortModel, tokenSplit } from '../lib/usageFormat'
import { useStore } from '../store'

/** Settings → Usage: today / week / month totals, tokens and cost per day, model and feature, plus per-model prices.
 *  Information only. Cost is shown only where a price is known; an unpriced call reads as unknown, never $0. */

const RANGES = [7, 30, 90] as const
const PERIODS: { id: UsagePeriod; label: string }[] = [{ id: 'today', label: 'Today' }, { id: 'week', label: 'This week' }, { id: 'month', label: 'This month' }]

export { money }
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

type Row = UsageBucket & { key: string; name: string; title?: string }

/** One line per model or feature: calls, input / output / cached tokens and cost (or "Unknown"). */
function Breakdown({ rows, label }: { rows: Row[]; label: string }): JSX.Element {
  return (
    <table className="usage-table">
      <thead><tr><th scope="col">{label}</th><th scope="col">Calls</th><th scope="col">Input</th><th scope="col">Output</th><th scope="col">Cached</th><th scope="col">Cost</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key}>
            <th scope="row" title={r.title}>{r.name}</th>
            <td>{r.calls.toLocaleString()}</td>
            <td>{compact(r.prompt_tokens)}</td>
            <td>{compact(r.completion_tokens)}</td>
            <td>{r.cached_tokens ? compact(r.cached_tokens) : '–'}</td>
            <td title={costNote(r) || undefined}>{costText(r)}</td>
          </tr>
        ))}
      </tbody>
    </table>
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
    draft[m]?.[side] ?? (report.prices[m]?.[side] != null ? String(report.prices[m][side]) : '')
  const edit = (m: string, side: 'input' | 'output', v: string): void =>
    setDraft((d) => ({ ...d, [m]: { input: valueOf(m, 'input'), output: valueOf(m, 'output'), [side]: v } }))

  const save = async (): Promise<void> => {
    setSaving('saving')
    // Only overrides and edited rows: list and proxy prices stay live instead of freezing into settings.
    const out = overridesToSave(models, report.prices, draft)
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
              <td className="usage-price-src">{draft[m] ? 'edited' : report.prices[m]?.source === 'override' ? 'custom' : report.prices[m]?.source === 'fireworks' ? 'Fireworks list price' : report.prices[m] ? 'from proxy' : 'no price'}</td>
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
  // A LiteLLM proxy publishes prices; Fireworks and other providers called directly do not.
  const proxy = useStore((st) => /\/\/(localhost|127\.0\.0\.1):4000\b/.test(st.settings.baseUrl ?? ''))
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
    const daily = report.daily.map((d) => ({ day: shortDay(d.day), cost: Number(d.cost.toFixed(6)), in: d.prompt_tokens, out: d.completion_tokens, cached: d.cached_tokens ?? 0, calls: d.calls }))
    return {
      tokens: spec({ type: 'bar', title: `Tokens per day (last ${report.days} days)`, x: 'day', stacked: true, series: [{ key: 'in', label: 'Input' }, { key: 'out', label: 'Output' }], data: daily }),
      cost: spec({ type: 'area', title: 'Cost per day (priced calls only)', x: 'day', series: ['cost'], unit: '$', data: daily }),
      calls: spec({ type: 'bar', title: 'Model calls per day', x: 'day', series: [{ key: 'calls', label: 'Model calls' }], data: daily }),
      hourly: spec({ type: 'bar', title: 'When you chat (hour of day)', x: 'hour', series: [{ key: 'calls', label: 'Chat calls' }], data: report.hourly.map((h) => ({ hour: hourLabel(h.hour), calls: h.calls })) }),
      weekday: spec({ type: 'bar', title: 'When you chat (day of week)', x: 'weekday', series: [{ key: 'calls', label: 'Chat calls' }], data: report.weekday }),
      byProject: spec({ type: 'pie', title: 'Tokens by project', x: 'project', series: [{ key: 'tokens', label: 'Tokens' }], data: report.by_project.slice(0, 8).map((p) => ({ project: p.project, tokens: p.tokens })) })
    }
  }, [report])

  if (error) return <div className="chart-err"><AlertCircle size={14} /> Couldn't load usage: {error}</div>
  if (!report || !charts) return <p className="muted small">{loading ? 'Loading usage…' : 'No usage recorded yet.'}</p>

  const t: UsageBucket = report.totals
  const empty = t.calls === 0
  const anyPriced = t.calls > t.unpriced
  const models: Row[] = report.by_model.map((m) => ({ ...m, key: m.model, name: shortModel(m.model), title: m.ids?.join(', ') || m.model }))
  const features: Row[] = (report.by_feature ?? []).map((f) => ({ ...f, key: f.feature, name: f.label }))

  return (
    <div className="usage">
      {report.periods && (
        <div className="usage-tiles usage-periods">
          {PERIODS.map(({ id, label }) => {
            const p = report.periods![id]
            return <Tile key={id} label={label} value={compact(p.tokens) + ' tokens'} sub={`${tokenSplit(p, compact)} · ${costText(p)}${costNote(p) ? ` (${costNote(p)})` : ''}`} />
          })}
        </div>
      )}

      <div className="usage-head">
        <div className="seg" role="group" aria-label="Range">
          {RANGES.map((d) => <button key={d} type="button" className={days === d ? 'active' : ''} aria-pressed={days === d} title={`Last ${d} days`} onClick={() => setDays(d)}>{d}d</button>)}
        </div>
        <span className="spacer" />
        <button className="icon-btn" aria-label="Refresh usage" title="Refresh" onClick={() => void load(days)}><RefreshCw size={13} className={loading ? 'spin' : ''} /></button>
      </div>

      {empty ? (
        <p className="muted small">No model calls in this range.</p>
      ) : (
        <>
          <div className="usage-tiles">
            <Tile label="Cost" value={costText(t)} sub={costNote(t) || `over ${report.days} days`} />
            <Tile label="Tokens" value={compact(t.tokens)} sub={tokenSplit(t, compact)} />
            <Tile label="Model calls" value={t.calls.toLocaleString()} sub={`${t.chat_calls} chat · ${t.learn_calls} memory${t.other_calls ? ` · ${t.other_calls} other` : ''}`} />
            <Tile label="Avg latency" value={ms(t.avg_ms)} sub="per model call" />
            <Tile label="Cache hit rate" value={pct(t.cache_hit_rate ?? 0)} sub={`${compact(t.cached_tokens ?? 0)} input tokens from the provider cache`} />
            <Tile label="Reasoning tokens" value={compact(t.reasoning_tokens ?? 0)} sub={`${pct(t.reasoning_share ?? 0)} of output tokens`} />
          </div>

          <div className="markdown usage-charts">
            <ChartBlock source={charts.tokens} streaming={false} />
            {anyPriced && <ChartBlock source={charts.cost} streaming={false} />}
          </div>

          <h4 className="usage-sub">By model</h4>
          <Breakdown rows={models} label="Model" />

          {features.length > 0 && (
            <>
              <h4 className="usage-sub">By feature</h4>
              <Breakdown rows={features} label="Feature" />
              <p className="muted small">Coding sessions (Claude Code, OpenCode) bill their own accounts, so their tokens are not counted here.</p>
            </>
          )}

          <details className="usage-more">
            <summary>More charts</summary>
            <div className="markdown usage-charts">
              <ChartBlock source={charts.calls} streaming={false} />
              <ChartBlock source={charts.hourly} streaming={false} />
              <ChartBlock source={charts.weekday} streaming={false} />
              {report.by_project.length > 1 && <ChartBlock source={charts.byProject} streaming={false} />}
            </div>
          </details>
        </>
      )}

      <h4>Prices</h4>
      <p className="muted small">
        {proxy
          ? 'Read from your LiteLLM proxy. A price you enter here wins. Saving re-prices the history.'
          : 'Fireworks list prices are built in. Other directly-called providers show cost as unknown until you enter a price ($ per million tokens). Saving re-prices the history; calls priced earlier keep their cost.'}
      </p>
      <PriceEditor report={report} onSaved={(prices) => setReport((r) => (r ? { ...r, prices } : r))} />
    </div>
  )
}
