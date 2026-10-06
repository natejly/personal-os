import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, Gauge, RefreshCw } from 'lucide-react'
import type { UsageReport } from '@shared/types'
import { api } from '../../lib/api'
import ChartBlock from '../../components/ChartBlock'
import { Tile, compact, money, ms, shortDay, spec } from '../../components/UsageView'
import type { WidgetDef, WidgetProps } from '../registry'

const RANGES = [7, 30, 90] as const

function UsageWidget({ window: win, live, onConfig }: WidgetProps): JSX.Element {
  const days = typeof win.config.days === 'number' ? win.config.days : 30
  const [report, setReport] = useState<UsageReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const fetched = useRef<number | null>(null)

  const load = useCallback(async (d: number) => {
    fetched.current = d
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

  // The only request this widget makes, and it is gated on `live`: an off-screen report is never fetched,
  // and coming back on-screen with the same range reuses what is already in state.
  useEffect(() => {
    if (!live || fetched.current === days) return
    void load(days)
  }, [live, days, load])

  const charts = useMemo(() => {
    if (!report) return null
    const daily = report.daily.map((d) => ({ day: shortDay(d.day), cost: Number(d.cost.toFixed(6)), in: d.prompt_tokens, out: d.completion_tokens }))
    const models = report.by_model.slice(0, 6).map((m) => ({ model: m.model, cost: Number(m.cost.toFixed(6)) }))
    return {
      cost: spec({ type: 'area', title: `Cost per day (${report.days}d)`, x: 'day', series: ['cost'], unit: '$', data: daily }),
      tokens: spec({ type: 'bar', title: 'Tokens per day', x: 'day', stacked: true, series: [{ key: 'in', label: 'Input' }, { key: 'out', label: 'Output' }], data: daily }),
      byModel: spec({ type: 'bar', title: 'Cost by model', x: 'model', series: [{ key: 'cost', label: 'Cost' }], unit: '$', data: models })
    }
  }, [report])

  // Recharts' ResponsiveContainer watches the DOM, so the charts come down with the window.
  if (!live) return <div className="widget"><div className="widget-empty">Usage · paused</div></div>

  const t = report?.totals
  return (
    <div className="widget">
      <div className="widget-bar">
        {RANGES.map((d) => (
          <button key={d} className={`widget-chip ${d === days ? 'on' : ''}`} onClick={() => onConfig({ days: d })}>{d}d</button>
        ))}
        <span className="spacer" />
        <button className="widget-chip" title="Refresh" onClick={() => void load(days)}><RefreshCw size={11} className={loading ? 'spin' : ''} /></button>
      </div>
      {error ? (
        <div className="widget-error"><span><AlertCircle size={14} /> Couldn't load usage: {error}</span></div>
      ) : !report || !charts || !t ? (
        <div className="widget-empty">{loading ? 'Loading usage…' : 'No usage recorded yet.'}</div>
      ) : t.calls === 0 ? (
        <div className="widget-empty">No model calls in this range.</div>
      ) : (
        <div className="widget-scroll">
          <div className="usage-tiles">
            <Tile label="Spend" value={money(t.cost)} sub={t.unpriced ? `${t.unpriced} unpriced` : `over ${report.days} days`} />
            <Tile label="Tokens" value={compact(t.tokens)} sub={`${compact(t.prompt_tokens)} in · ${compact(t.completion_tokens)} out`} />
            <Tile label="Calls" value={String(t.calls)} sub={`${t.chat_calls} chat · ${t.learn_calls} learn`} />
            <Tile label="Latency" value={ms(t.avg_ms)} sub="per model call" />
          </div>
          <div className="markdown usage-charts">
            <ChartBlock source={charts.cost} streaming={false} />
            <ChartBlock source={charts.tokens} streaming={false} />
            <ChartBlock source={charts.byModel} streaming={false} />
          </div>
        </div>
      )}
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'usage',
  label: 'Usage',
  icon: <Gauge size={15} />,
  defaultSize: { w: 560, h: 420 },
  minSize: { w: 360, h: 280 },
  chrome: 'full',
  heavy: true,
  defaultConfig: { days: 30 },
  Component: UsageWidget
}
