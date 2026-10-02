import { useCallback, useEffect, useState } from 'react'
import { Activity, Droplet, Footprints, Gauge, HeartPulse, Link2, Moon, PanelLeftOpen, Pill, Plus, Scale, Settings2, Smile, Trash2, Zap } from 'lucide-react'
import type { HealthEntry, HealthMetric, HealthSummary } from '@shared/types'
import { useStore } from '../../store'
import { api } from '../../lib/api'
import { lines, usePageContext } from '../../lib/pageContext'
import { localDay } from '../../components/CalendarWeek'
import AppSwitcher from '../../components/AppSwitcher'
import { Sparkline, TrendChart } from './charts'
import Sources from './Sources'
import { dayLabel, delta, fmt, goalText, meets, progress } from './format'
import '../../styles/health.css'

const ICONS: Record<string, JSX.Element> = {
  sleep: <Moon size={14} />, steps: <Footprints size={14} />, water: <Droplet size={14} />, exercise: <Activity size={14} />,
  weight: <Scale size={14} />, resting_hr: <HeartPulse size={14} />, mood: <Smile size={14} />, energy: <Zap size={14} />, meds: <Pill size={14} />
}
export const metricIcon = (key: string): JSX.Element => ICONS[key] ?? <Gauge size={14} />

const mean = (ps: { value: number | null }[]): number | null => (ps.length ? ps.reduce((a, p) => a + (p.value ?? 0), 0) / ps.length : null)

const RANGES = [7, 30, 90] as const
type Range = (typeof RANGES)[number]
const SPARK_DAYS = 14

export default function HealthView(): JSX.Element {
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const { toggleSidebar, toast, refreshDashboard } = useStore()
  const [days, setDays] = useState<Range>(30)
  const [today, setToday] = useState(localDay())
  const [rows, setRows] = useState<HealthSummary[] | null>(null)
  const [sel, setSel] = useState<string | null>(null)
  const [entries, setEntries] = useState<HealthEntry[]>([])
  const [manage, setManage] = useState(false)
  const [connect, setConnect] = useState(false)

  // Sparklines need 14 days even when the range is a week, so fetch at least that much and slice.
  const load = useCallback(async () => {
    const d = localDay()
    try {
      setRows(await api.healthLog.summary(Math.max(days, SPARK_DAYS), d))
      setToday(d)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }, [days, toast])
  useEffect(() => { void load() }, [load])

  const selected = rows?.find((r) => r.key === sel) ?? rows?.[0] ?? null
  const loadEntries = useCallback(async () => {
    if (selected) setEntries(await api.healthLog.entries(selected.key, 60).catch(() => []))
  }, [selected?.key]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { void loadEntries() }, [loadEntries])

  const changed = async (): Promise<void> => {
    await Promise.all([load(), loadEntries()])
    void refreshDashboard()
  }
  const log = async (metric: string, value: number, day?: string, note?: string): Promise<boolean> => {
    try {
      await api.healthLog.log({ metric, value, day, note })
    } catch (e) {
      toast((e as Error).message, 'error')
      return false
    }
    await changed()
    return true
  }

  usePageContext(() => {
    if (!rows) return null
    const line = (m: HealthSummary): string => {
      const parts = [`today ${fmt(m, m.today)}`]
      if (m.avg != null) parts.push(`${days}-day avg ${fmt(m, m.avg)}`)
      const g = goalText(m)
      if (g) parts.push(`${g.toLowerCase()}, ${m.streak}-day streak`)
      if (m.today == null && m.last) parts.push(`last ${fmt(m, m.last.value)} on ${m.last.day}`)
      return `${m.label} (\`${m.key}\`): ${parts.join('; ')}`
    }
    return {
      view: 'health',
      label: 'Health',
      detail: `Today is ${today}.\n${lines(rows, line)}`,
      refs: rows.map((m) => ({ kind: 'health_metric', id: m.key, name: m.label })),
      hints: ['How has my sleep been this month?', 'Log 7.5 hours of sleep for last night', 'Does my mood track my exercise?']
    }
  }, [rows, today, days])

  return (
    <main className="page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" aria-label="Show sidebar" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><HeartPulse size={16} /> Health</h2>
        <div className="no-drag header-right">
          <div className="seg" role="group" aria-label="Range">
            {RANGES.map((r) => <button key={r} className={days === r ? 'active' : ''} onClick={() => setDays(r)}>{r}d</button>)}
          </div>
          <button className={`icon-btn ${connect ? 'on' : ''}`} title="Connected services (COROS, Garmin)" aria-label="Connected services" aria-pressed={connect} onClick={() => setConnect((v) => !v)}>
            <Link2 size={15} />
          </button>
          <button className={`icon-btn ${manage ? 'on' : ''}`} title="Choose and edit metrics" aria-label="Choose and edit metrics" aria-pressed={manage} onClick={() => setManage((v) => !v)}>
            <Settings2 size={15} />
          </button>
        </div>
        <AppSwitcher />
      </header>
      <div className="page-body hl-body">
        {connect && <Sources onSynced={changed} onClose={() => setConnect(false)} />}
        {manage && <MetricManager onChanged={changed} onClose={() => setManage(false)} />}
        {!rows ? <p className="empty-hint big">Loading…</p> : rows.length === 0 ? (
          <div className="empty-hint big"><p>Every metric is hidden.</p><button className="ghost-btn" onClick={() => setManage(true)}>Choose metrics</button></div>
        ) : (
          <>
            <div className="hl-tiles">
              {rows.map((m) => (
                <Tile key={m.key} m={m} today={today} selected={selected?.key === m.key} onSelect={() => setSel(m.key)} onLog={(v) => log(m.key, v)} />
              ))}
            </div>
            {selected && <Detail full={selected} days={days} today={today} entries={entries} onLog={log} onChanged={changed} />}
          </>
        )}
      </div>
    </main>
  )
}

function Tile({ m, today, selected, onSelect, onLog }: {
  m: HealthSummary; today: string; selected: boolean; onSelect: () => void; onLog: (v: number) => Promise<boolean>
}): JSX.Element {
  const p = progress(m, m.today)
  const met = meets(m, m.today)
  const stale = m.today == null && m.last ? `${fmt(m, m.last.value)} · ${dayLabel(m.last.day, today)}` : null
  return (
    <section className={`hl-tile ${selected ? 'sel' : ''}`}>
      <button className="hl-tile-head" onClick={onSelect} aria-pressed={selected} title={`Show ${m.label} history`}>
        <span className="hl-tile-label">{metricIcon(m.key)} {m.label}</span>
        <span className="hl-tile-value">{m.today == null ? <span className="hl-none">—</span> : fmt(m, m.today)}</span>
        <span className="hl-tile-sub">
          {stale ?? (m.goal != null && m.kind !== 'check' ? goalText(m) : m.kind === 'check' ? 'Today' : `${m.agg === 'sum' ? 'Total' : m.agg === 'avg' ? 'Average' : 'Latest'} today`)}
          {m.streak > 1 && <span className="hl-streak"> · {m.streak}-day streak</span>}
        </span>
        {p != null && m.kind !== 'check' && (
          <span className="hl-meter" role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={p} aria-label={`${Math.round(p * 100)}% of ${m.goal_dir === 'at_most' ? 'limit' : 'goal'}`}>
            <span className={`hl-meter-fill ${m.goal_dir === 'at_most' && met === false ? 'over' : ''}`} style={{ width: `${p * 100}%` }} />
          </span>
        )}
        <Sparkline metric={m} points={m.series.slice(-SPARK_DAYS)} />
      </button>
      <QuickLog m={m} onLog={onLog} />
    </section>
  )
}

/** The one-tap log on a tile: a number box, the five scale steps, or Yes / No. */
function QuickLog({ m, onLog }: { m: HealthSummary; onLog: (v: number) => Promise<boolean> }): JSX.Element {
  const [v, setV] = useState('')
  const [busy, setBusy] = useState(false)
  const go = async (n: number): Promise<void> => {
    setBusy(true)
    const ok = await onLog(n)
    setBusy(false)
    if (ok) setV('')
  }
  if (m.kind === 'check') {
    return (
      <div className="seg hl-quick" role="group" aria-label={`${m.label} today`}>
        {[1, 0].map((n) => <button key={n} disabled={busy} className={m.today === n ? 'on' : ''} onClick={() => void go(n)}>{n ? 'Yes' : 'No'}</button>)}
      </div>
    )
  }
  if (m.kind === 'scale') {
    // Lit: the step last tapped today. The tile's number is the day's average, which can sit between taps.
    const cur = m.last?.day === m.series[m.series.length - 1]?.day ? Math.round(m.last.value) : null
    return (
      <div className="seg hl-quick" role="group" aria-label={`Log ${m.label}, 1 to 5`}>
        {[1, 2, 3, 4, 5].map((n) => <button key={n} disabled={busy} className={cur === n ? 'on' : ''} title={`Log ${n}/5`} onClick={() => void go(n)}>{n}</button>)}
      </div>
    )
  }
  const n = Number(v)
  const ok = v.trim() !== '' && Number.isFinite(n) && n >= 0
  return (
    <form className="hl-quick hl-quick-num" onSubmit={(e) => { e.preventDefault(); if (ok) void go(n) }}>
      <input type="number" inputMode="decimal" min={0} step="any" value={v} onChange={(e) => setV(e.target.value)}
        placeholder={m.unit || 'value'} aria-label={`${m.agg === 'sum' ? 'Add to' : 'Log'} ${m.label}${m.unit ? ` (${m.unit})` : ''}`} />
      <button type="submit" className="ghost-btn" disabled={!ok || busy}>{m.agg === 'sum' ? <><Plus size={13} /> Add</> : 'Log'}</button>
    </form>
  )
}

function Detail({ full, days, today, entries, onLog, onChanged }: {
  full: HealthSummary; days: number; today: string; entries: HealthEntry[]
  onLog: (metric: string, value: number, day?: string, note?: string) => Promise<boolean>; onChanged: () => Promise<void>
}): JSX.Element {
  const toast = useStore((s) => s.toast)
  // The fetch covers at least 14 days, so on the 7-day range the week before is already in the series;
  // otherwise the fetched window is the range and the backend's prev_avg is the window before it.
  const m = { ...full, series: full.series.slice(-days) }
  const logged = m.series.filter((p) => p.value != null)
  const avg = mean(logged)
  const met = m.goal != null ? logged.filter((p) => meets(m, p.value)).length : null
  const prev = full.series.length >= 2 * days ? mean(full.series.slice(-2 * days, -days).filter((p) => p.value != null)) : full.prev_avg
  const d = delta(m, avg, prev)
  const remove = async (id: string): Promise<void> => {
    try {
      await api.healthLog.deleteEntry(id)
    } catch (e) {
      return toast((e as Error).message, 'error')
    }
    await onChanged()
  }
  return (
    <section className="hl-detail" aria-label={`${m.label} history`}>
      <header className="hl-detail-head">
        <h3>{metricIcon(m.key)} {m.label}</h3>
        <dl className="hl-stats">
          {m.kind === 'check'
            ? <div><dt>Days yes</dt><dd>{logged.filter((p) => p.value! >= 1).length} of {days}</dd></div>
            : <div><dt>{days}-day {m.agg === 'sum' ? 'daily avg' : 'avg'}</dt><dd>{fmt(m, avg)}{d && <span className="hl-delta"> {d} vs previous {days}d</span>}</dd></div>}
          <div><dt>Logged</dt><dd>{logged.length} of {days} days</dd></div>
          {met != null && m.kind !== 'check' && <div><dt>{m.goal_dir === 'at_most' ? 'Under limit' : 'Met goal'}</dt><dd>{met} days</dd></div>}
          {m.goal != null && <div><dt>Streak</dt><dd>{m.streak} {m.streak === 1 ? 'day' : 'days'}</dd></div>}
        </dl>
      </header>
      <TrendChart metric={m} today={today} />
      <LogForm m={m} today={today} onLog={onLog} />
      <h4 className="section-h">Entries <span>{entries.length}</span></h4>
      {entries.length === 0 ? <p className="empty-hint">Nothing logged yet.</p> : (
        <table className="hl-entries">
          <thead><tr><th>Day</th><th>Value</th><th>Note</th><th aria-label="Actions" /></tr></thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id}>
                <td>{dayLabel(e.day, today)}</td>
                <td className="num">{fmt(m, e.value)}</td>
                <td className="note">{e.note}{e.source !== 'manual' && <span className="hl-src" title={e.source === 'assistant' ? 'Logged by the assistant' : `Synced from ${e.source}`}> · {e.source === 'assistant' ? 'assistant' : e.source.toUpperCase() === 'COROS' ? 'COROS' : e.source[0].toUpperCase() + e.source.slice(1)}</span>}</td>
                <td><button className="icon-btn" aria-label={`Delete ${fmt(m, e.value)} on ${e.day}`} title="Delete" onClick={() => void remove(e.id)}><Trash2 size={13} /></button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

/** Log against any day, with a note: last night's sleep, a missed day, a reading with context. */
function LogForm({ m, today, onLog }: { m: HealthSummary; today: string; onLog: (metric: string, value: number, day?: string, note?: string) => Promise<boolean> }): JSX.Element {
  const [day, setDay] = useState(today)
  const [v, setV] = useState(m.kind === 'number' ? '' : m.kind === 'scale' ? '3' : '1')
  const [note, setNote] = useState('')
  useEffect(() => { setV(m.kind === 'number' ? '' : m.kind === 'scale' ? '3' : '1'); setNote('') }, [m.key, m.kind])
  const n = Number(v)
  const ok = v.trim() !== '' && Number.isFinite(n) && n >= 0 && day !== ''
  const submit = async (): Promise<void> => {
    if (!ok) return
    if (await onLog(m.key, n, day, note)) {
      setNote('')
      if (m.kind === 'number') setV('')
    }
  }
  return (
    <form className="add-row hl-log" onSubmit={(e) => { e.preventDefault(); void submit() }}>
      <input type="date" aria-label="Day" value={day} max={today} onChange={(e) => setDay(e.target.value)} className="date-input" />
      {m.kind === 'number'
        ? <input type="number" inputMode="decimal" min={0} step="any" aria-label={`${m.label}${m.unit ? ` (${m.unit})` : ''}`} placeholder={m.unit || 'value'} value={v} onChange={(e) => setV(e.target.value)} className="hl-log-val" />
        : <select aria-label={m.label} value={v} onChange={(e) => setV(e.target.value)}>
            {m.kind === 'scale' ? [1, 2, 3, 4, 5].map((k) => <option key={k} value={k}>{k}/5</option>) : <><option value="1">Yes</option><option value="0">No</option></>}
          </select>}
      <input type="text" aria-label="Note (optional)" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
      <button type="submit" className="primary-btn" disabled={!ok}><Plus size={14} /> Log</button>
    </form>
  )
}

/** Show/hide, rename, re-unit and re-goal metrics; add custom ones. `kind` is fixed once created. */
function MetricManager({ onChanged, onClose }: { onChanged: () => Promise<void>; onClose: () => void }): JSX.Element {
  const toast = useStore((s) => s.toast)
  const [metrics, setMetrics] = useState<HealthMetric[]>([])
  const reload = useCallback(async () => setMetrics(await api.healthLog.metrics()), [])
  useEffect(() => { void reload() }, [reload])
  const run = async (f: () => Promise<unknown>): Promise<void> => {
    try {
      await f()
    } catch (e) {
      return toast((e as Error).message, 'error')
    }
    await Promise.all([reload(), onChanged()])
  }
  return (
    <section className="hl-manage" aria-label="Metrics">
      <header className="hl-manage-head">
        <h3>Metrics</h3>
        <span className="muted small">Shown metrics appear as tiles and on Today. Hidden ones keep their history.</span>
        <button className="ghost-btn" onClick={onClose}>Done</button>
      </header>
      <div className="hl-manage-list">
        {metrics.map((m) => <MetricRow key={m.key} m={m} run={run} />)}
      </div>
      <NewMetric run={run} />
    </section>
  )
}

function MetricRow({ m, run }: { m: HealthMetric; run: (f: () => Promise<unknown>) => Promise<void> }): JSX.Element {
  const [label, setLabel] = useState(m.label)
  const [unit, setUnit] = useState(m.unit)
  const [dir, setDir] = useState<string>(m.goal_dir ?? '')
  const [goal, setGoal] = useState(m.goal == null ? '' : String(m.goal))
  useEffect(() => { setLabel(m.label); setUnit(m.unit); setDir(m.goal_dir ?? ''); setGoal(m.goal == null ? '' : String(m.goal)) }, [m])
  const goalOk = !dir || (goal.trim() !== '' && Number.isFinite(Number(goal)))
  const dirty = label !== m.label || unit !== m.unit || dir !== (m.goal_dir ?? '') || (dir !== '' && Number(goal) !== m.goal)
  const save = (): Promise<void> => run(() => api.healthLog.updateMetric(m.key, {
    label, unit,
    ...(dir ? { goal: Number(goal), goal_dir: dir as 'at_least' | 'at_most' } : { clear_goal: true })
  }))
  return (
    <div className={`hl-mrow ${m.hidden ? 'off' : ''}`}>
      <label className="check" title={m.hidden ? 'Show' : 'Hide'}>
        <input type="checkbox" checked={!m.hidden} onChange={(e) => void run(() => api.healthLog.updateMetric(m.key, { hidden: !e.target.checked }))} aria-label={`Show ${m.label}`} />
      </label>
      <span className="hl-mrow-icon">{metricIcon(m.key)}</span>
      <input value={label} onChange={(e) => setLabel(e.target.value)} aria-label="Name" className="hl-in name" />
      {m.kind === 'number'
        ? <input value={unit} onChange={(e) => setUnit(e.target.value)} aria-label="Unit" placeholder="unit" className="hl-in unit" />
        : <span className="hl-kind">{m.kind === 'scale' ? '1–5' : 'yes/no'}</span>}
      <select value={dir} onChange={(e) => setDir(e.target.value)} aria-label="Goal">
        <option value="">No goal</option>
        <option value="at_least">At least</option>
        <option value="at_most">At most</option>
      </select>
      {dir && m.kind !== 'check' && <input type="number" min={0} step="any" value={goal} onChange={(e) => setGoal(e.target.value)} aria-label="Goal value" className="hl-in goal" />}
      {dir && m.kind === 'check' && <span className="hl-kind">every day</span>}
      <span className="hl-mrow-actions">
        {dirty && <button className="primary-btn" disabled={!goalOk || !label.trim()} onClick={() => void save()}>Save</button>}
        {!m.builtin && (
          <button className="icon-btn" title="Delete metric and its history" aria-label={`Delete ${m.label}`}
            onClick={() => { if (window.confirm(`Delete “${m.label}” and every reading logged for it?`)) void run(() => api.healthLog.deleteMetric(m.key)) }}>
            <Trash2 size={13} />
          </button>
        )}
      </span>
    </div>
  )
}

function NewMetric({ run }: { run: (f: () => Promise<unknown>) => Promise<void> }): JSX.Element {
  const [label, setLabel] = useState('')
  const [kind, setKind] = useState<HealthMetric['kind']>('number')
  const [unit, setUnit] = useState('')
  const [agg, setAgg] = useState<HealthMetric['agg']>('sum')
  const add = async (): Promise<void> => {
    if (!label.trim()) return
    await run(() => api.healthLog.createMetric({
      label, kind, unit: kind === 'number' ? unit : kind === 'scale' ? '/5' : '',
      agg: kind === 'number' ? agg : kind === 'scale' ? 'avg' : 'last',
      decimals: kind === 'scale' ? 1 : 0,
      ...(kind === 'check' ? { goal: 1, goal_dir: 'at_least' as const } : {})
    }))
    setLabel(''); setUnit('')
  }
  return (
    <form className="add-row hl-new" onSubmit={(e) => { e.preventDefault(); void add() }}>
      <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="New metric, e.g. Caffeine, Vitamin D, Meditation" aria-label="New metric name" />
      <select value={kind} onChange={(e) => setKind(e.target.value as HealthMetric['kind'])} aria-label="Kind">
        <option value="number">Number</option>
        <option value="scale">1–5 scale</option>
        <option value="check">Yes / no</option>
      </select>
      {kind === 'number' && <>
        <input value={unit} onChange={(e) => setUnit(e.target.value)} placeholder="unit" aria-label="Unit" className="hl-in unit" />
        <select value={agg} onChange={(e) => setAgg(e.target.value as HealthMetric['agg'])} aria-label="Per day" title="How several readings in one day combine">
          <option value="sum">Adds up</option>
          <option value="last">Latest reading</option>
          <option value="avg">Average</option>
        </select>
      </>}
      <button type="submit" className="primary-btn" disabled={!label.trim()}><Plus size={14} /> Add</button>
    </form>
  )
}
