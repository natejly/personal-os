import { useEffect, useMemo, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  AlertTriangle, BellOff, Brain, CalendarClock, Check, ChevronRight, Clock, Eye, EyeOff, FileText,
  Globe, Keyboard, Lightbulb, ListPlus, MonitorDot, Mic, PanelLeftOpen, Pause, Play, RefreshCw,
  Repeat, Send, Shield, Speaker, Sparkles, Trash2, TrendingUp, X, Zap
} from 'lucide-react'
import { useStore } from '../store'
import type {
  ActivityCapability, ActivityEvent, ActivityHabit, ActivityInsights, ActivityPattern,
  ActivitySignal, ActivityStatus, ActivitySuggestion, InsightKind
} from '@shared/types'
import AppSwitcher from './AppSwitcher'

/** The activity monitor: what it records, what it inferred, and every switch that turns it off.
 *
 *  The panel is deliberately blunt about cost to privacy. Each signal says in plain words what it
 *  captures before you can turn it on, the raw log is browsable and row-deletable, and the purge
 *  controls are one click away - because the only way this feature is reasonable to ship is if the
 *  person running it can always see exactly what it knows. */

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'insights', label: 'Insights' },
  { key: 'signals', label: 'Signals' },
  { key: 'privacy', label: 'Privacy' },
  { key: 'context', label: 'Context file' },
  { key: 'log', label: 'Raw log' }
] as const
type Tab = (typeof TABS)[number]['key']

/** Each signal, with an honest description of exactly what lands in the database. */
const SIGNAL_INFO: Record<ActivitySignal, { label: string; icon: JSX.Element; what: string; heavy?: boolean }> = {
  apps: {
    label: 'Apps and windows', icon: <MonitorDot size={15} />,
    what: 'Which app is in front and the title of its focused window, sampled every few seconds and stored as stretches of attention with durations.'
  },
  browserUrls: {
    label: 'Browser URLs', icon: <Globe size={15} />,
    what: 'The address of the active tab in Safari, Chrome, Arc, Brave, Edge or Vivaldi. Full URLs, including query strings.'
  },
  input: {
    label: 'Typing and clicks (counts only)', icon: <Keyboard size={15} />,
    what: 'How many keystrokes, clicks and scrolls, and your typing speed. No characters, no key names beyond return/tab/delete/escape.'
  },
  text: {
    label: 'The text you actually type', icon: <FileText size={15} />, heavy: true,
    what: 'Every character you type, in any app, as a rolling buffer. Redaction strips credential- and PII-shaped strings first, and capture stops entirely while macOS reports a password field. This is a keylogger; treat it that way.'
  },
  micAudio: {
    label: 'Microphone', icon: <Mic size={15} />, heavy: true,
    what: 'Records your microphone in short chunks, transcribes each one, then deletes the audio. Only the text is kept. It cannot tell whose voice it is, so it will transcribe people around you too.'
  },
  outputAudio: {
    label: 'System audio', icon: <Speaker size={15} />, heavy: true,
    what: 'Same as the microphone, for whatever your speakers played - calls, videos, music. Needs a loopback device; macOS will not record its own output otherwise.'
  }
}

const KIND_LABEL: Record<string, string> = {
  focus: 'window', input: 'typing', idle: 'away', audio: 'audio', note: 'note'
}

const fmtDur = (ms: number): string => {
  const s = ms / 1000
  if (s < 60) return `${Math.round(s)}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  return `${(s / 3600).toFixed(1)}h`
}
const clock = (ts: number): string => new Date(ts * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
const clockSec = (ts: number): string => new Date(ts * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
const ago = (ts: number | null): string => {
  if (!ts) return 'never'
  const s = Date.now() / 1000 - ts
  return s < 60 ? 'just now' : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`
}

const STATE_LABEL: Record<string, string> = {
  granted: 'granted', denied: 'denied', unasked: 'not asked yet', unknown: 'unknown', 'n/a': 'not needed here'
}

/** The checklist, with the two things that can actually fix a row: ask macOS, or open the pane.
 *
 *  Grant is the only button in the app that can make a system dialog appear, which is why it is a
 *  button and not something the panel does on load. macOS shows most of these once per app ever,
 *  so when it stays silent the row falls back to the pane and says to restart afterwards. */
function Capabilities({ caps, onGrant, onOpen }: {
  caps: ActivityCapability[]
  onGrant: (id: string, browser?: string) => void
  onOpen: (id: string) => void
}): JSX.Element {
  const bad = caps.filter((c) => !c.ok)
  const blocking = bad.filter((c) => !c.optional)
  const [open, setOpen] = useState(bad.length > 0)
  const grantable = caps.filter((c) => c.requestable && c.state !== 'granted' && c.id !== 'automation')
  return (
    <section className="act-card">
      <button className="act-card-head" onClick={() => setOpen((o) => !o)}>
        <ChevronRight size={13} className={open ? 'rot90' : ''} />
        <b>Access on this machine</b>
        <span className={`act-pill ${blocking.length ? 'warn' : bad.length ? '' : 'ok'}`}>
          {blocking.length ? `${blocking.length} blocking` : bad.length ? `${bad.length} optional missing` : 'full access'}
        </span>
      </button>
      {open && (
        <>
          {grantable.length > 1 && (
            <div className="act-grant-all">
              <button className="primary-btn sm" onClick={() => grantable.forEach((c) => onGrant(c.id))}>
                <Shield size={13} /> Ask for everything missing
              </button>
              <span className="muted small">
                macOS asks one dialog at a time, and the grant lands on the app bundle — Personal OS, or Electron in
                a dev build. Restart the app afterwards so the keystroke tap is created with the grants in place.
              </span>
            </div>
          )}
          <ul className="act-caps">
            {caps.map((c) => (
              <li key={c.id} className={c.ok ? 'ok' : c.optional ? 'warn' : 'bad'}>
                {c.ok ? <Check size={13} /> : <AlertTriangle size={13} />}
                <div>
                  <b>{c.label}</b>
                  {c.state && <span className={`act-state-pill ${c.state}`}>{STATE_LABEL[c.state] ?? c.state}</span>}
                  {c.optional && !c.ok && <span className="act-state-pill opt">optional</span>}
                  {c.signals.length > 0 && <span className="muted small"> gates {c.signals.join(', ')}</span>}
                  <p>{c.detail}</p>
                  {!c.ok && c.fix && <p className="act-fix">{c.fix}</p>}
                  {(c.requestable || c.settings_url) && (
                    <div className="act-cap-actions">
                      {c.id === 'automation'
                        ? c.extra.map((b) => (
                            <button key={b.name} className="ghost-btn xs" disabled={b.state === 'granted'}
                              onClick={() => onGrant('automation', b.name)}>
                              {b.state === 'granted' ? <Check size={11} /> : <Shield size={11} />} {b.name}
                            </button>
                          ))
                        : c.requestable && c.state !== 'granted' && (
                            <button className="ghost-btn xs" onClick={() => onGrant(c.id)}><Shield size={11} /> Grant</button>
                          )}
                      {c.settings_url && (
                        <button className="link xs" onClick={() => onOpen(c.id)}>Open System Settings</button>
                      )}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

/** Palantir mode: one switch for "record everything", and an honest account of what that costs.
 *
 *  It is gated behind a typed confirmation because it is the one control in the app that turns
 *  protections off rather than on - and it says out loud which protection it cannot touch. */
function PalantirCard({ on, missing, onSet }: { on: boolean; missing: string[]; onSet: (on: boolean) => void }): JSX.Element {
  const [confirming, setConfirming] = useState(false)
  return (
    <section className={`act-card palantir ${on ? 'armed' : ''}`}>
      <div className="act-card-head static">
        <Eye size={14} />
        <b>Palantir mode</b>
        {on && <span className="act-pill warn">recording everything</span>}
      </div>
      <div className="act-palantir-body">
        <p className="muted small">
          One switch for everything: all six signals on — including the keylogger, the microphone and system audio —
          redaction off, and both “never record” lists emptied, so password managers and sign-in pages get recorded
          like any other window.
        </p>
        <p className="muted small">
          The one protection it cannot turn off is secure input: while macOS reports a focused password field it
          withholds keystrokes from every tap in the system, so those keys are never ours to record. The count of
          dropped keys still shows up in the log.
        </p>
        <p className="muted small">
          Turning it off restores the exclusion lists and signal choices you had before — they are snapshotted on
          the way in, not reset to defaults.
        </p>
        {on && missing.length > 0 && (
          <p className="act-warn"><AlertTriangle size={13} /> Recording everything it can, but macOS is still
            withholding: {missing.join(', ')}. Grant those above, then restart the app.</p>
        )}
        {on
          ? <button className="ghost-btn danger" onClick={() => onSet(false)}><EyeOff size={14} /> Turn Palantir mode off</button>
          : confirming
            ? (
              <div className="act-palantir-confirm">
                <p><b>Record everything, with the filters down?</b></p>
                <div className="act-danger">
                  <button className="ghost-btn danger" onClick={() => { setConfirming(false); onSet(true) }}>
                    <Eye size={13} /> Yes, record everything
                  </button>
                  <button className="ghost-btn" onClick={() => setConfirming(false)}>Cancel</button>
                </div>
              </div>
            )
            : <button className="ghost-btn danger" onClick={() => setConfirming(true)}><Eye size={14} /> Turn Palantir mode on</button>}
      </div>
    </section>
  )
}

function NumberField({ label, hint, value, min, max, step = 1, suffix, onCommit }: {
  label: string; hint?: string; value: number; min: number; max: number; step?: number; suffix: string
  onCommit: (v: number) => void
}): JSX.Element {
  const [draft, setDraft] = useState(String(value))
  useEffect(() => setDraft(String(value)), [value])
  const commit = (): void => {
    const n = Number(draft)
    if (Number.isFinite(n) && n >= min && n <= max) onCommit(n)
    else setDraft(String(value))
  }
  return (
    <label className="act-field">
      <span><b>{label}</b>{hint && <small>{hint}</small>}</span>
      <span className="act-num">
        <input type="number" min={min} max={max} step={step} value={draft}
          onChange={(e) => setDraft(e.target.value)} onBlur={commit}
          onKeyDown={(e) => { if (e.key === 'Enter') commit() }} />
        <em>{suffix}</em>
      </span>
    </label>
  )
}

/** Comma-free tag editor for the exclusion lists. */
function ListEditor({ label, hint, items, placeholder, onChange }: {
  label: string; hint: string; items: string[]; placeholder: string; onChange: (v: string[]) => void
}): JSX.Element {
  const [draft, setDraft] = useState('')
  const add = (): void => {
    const v = draft.trim()
    if (v && !items.some((i) => i.toLowerCase() === v.toLowerCase())) onChange([...items, v])
    setDraft('')
  }
  return (
    <div className="act-list-editor">
      <b>{label}</b>
      <p className="muted small">{hint}</p>
      <div className="act-tags">
        {items.map((i) => (
          <span key={i} className="act-tag">
            {i}
            <button className="icon-btn ghost xs" title={`Stop excluding ${i}`} onClick={() => onChange(items.filter((x) => x !== i))}><X size={11} /></button>
          </span>
        ))}
      </div>
      <div className="act-add">
        <input value={draft} placeholder={placeholder} onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} />
        <button className="ghost-btn" onClick={add} disabled={!draft.trim()}>Add</button>
      </div>
    </div>
  )
}

const KIND_META: Record<InsightKind, { label: string; icon: JSX.Element; blurb: string }> = {
  automation: {
    label: 'Automate', icon: <Zap size={13} />,
    blurb: 'Something the app can take over for you.'
  },
  hygiene: {
    label: 'Working pattern', icon: <TrendingUp size={13} />,
    blurb: 'Not an automation — a change to how the day is shaped.'
  },
  platform: {
    label: 'Grain itself', icon: <Lightbulb size={13} />,
    blurb: 'Friction visible in your data that the app should remove. Keep it as a note for later.'
  }
}

const PATTERN_ICON: Record<string, JSX.Element> = {
  app_routine: <Clock size={13} />, site_habit: <Globe size={13} />, thrash: <Repeat size={13} />,
  deep_work: <Brain size={13} />, day_shape: <CalendarClock size={13} />, after_hours: <Clock size={13} />,
  input_load: <Keyboard size={13} />, recurring_window: <MonitorDot size={13} />,
  topic: <Sparkles size={13} />, switch_rate: <Repeat size={13} />
}

/** What the primary button on a suggestion does — and what it deliberately does not do. */
const ACTION_LABEL: Record<string, { label: string; icon: JSX.Element }> = {
  prompt: { label: 'Set it up in chat', icon: <Send size={13} /> },
  todo: { label: 'Add as todo', icon: <ListPlus size={13} /> },
  memory: { label: 'Save to memory', icon: <Brain size={13} /> },
  setting: { label: 'Got it', icon: <Check size={13} /> },
  none: { label: 'Got it', icon: <Check size={13} /> }
}

/** Plain horizontal bars. No chart library for six rows of one number each. */
function Bars({ rows, fmt }: { rows: { label: string; value: number }[]; fmt: (v: number) => string }): JSX.Element {
  const max = Math.max(1, ...rows.map((r) => r.value))
  return (
    <ul className="act-bars">
      {rows.map((r) => (
        <li key={r.label}>
          <span className="act-bar-label" title={r.label}>{r.label}</span>
          <span className="act-bar-track"><span className="act-bar-fill" style={{ width: `${(r.value / max) * 100}%` }} /></span>
          <span className="act-bar-value">{fmt(r.value)}</span>
        </li>
      ))}
    </ul>
  )
}

/** The 24 hours of the day, so "mornings" is something you can see rather than take on trust. */
function HoursStrip({ hours }: { hours: { hour: number; seconds: number }[] }): JSX.Element {
  const max = Math.max(1, ...hours.map((h) => h.seconds))
  return (
    <div className="act-hours">
      {hours.map((h) => (
        <span key={h.hour} title={`${String(h.hour).padStart(2, '0')}:00 — ${fmtDur(h.seconds * 1000)}`}>
          <i style={{ height: `${Math.max(2, (h.seconds / max) * 100)}%` }} />
          {h.hour % 6 === 0 && <em>{String(h.hour).padStart(2, '0')}</em>}
        </span>
      ))}
    </div>
  )
}

function HabitRow({ h, onForget }: { h: ActivityHabit; onForget: () => void }): JSX.Element {
  return (
    <li className="act-habit">
      <span className="act-conf" title={`confidence ${Math.round(h.confidence * 100)}%`}>
        {Math.round(h.confidence * 100)}%
      </span>
      <span className="act-habit-text">
        {h.statement}
        <small>
          {h.memory_id
            ? <><Brain size={11} /> in memory, so chats already know it</>
            : <>not written to memory (below the confidence floor)</>}
          {h.support > 1 && ` · seen in ${h.support} passes`}
        </small>
      </span>
      <button className="icon-btn ghost danger xs" title="Forget this, and the memory it wrote" onClick={onForget}>
        <Trash2 size={12} />
      </button>
    </li>
  )
}

function SuggestionCard({ s, patterns, onApply, onStatus }: {
  s: ActivitySuggestion
  patterns: Record<string, ActivityPattern>
  onApply: () => void
  onStatus: (status: 'dismissed' | 'snoozed' | 'new') => void
}): JSX.Element {
  const meta = KIND_META[s.kind] ?? KIND_META.automation
  const act = ACTION_LABEL[s.action?.type ?? 'none'] ?? ACTION_LABEL.none
  const ruled = s.status !== 'new'
  return (
    <article className={`act-sug ${s.kind} ${ruled ? 'ruled' : ''}`}>
      <header>
        <span className={`act-pill ${s.kind}`}>{meta.icon} {meta.label}</span>
        <b>{s.title}</b>
        {s.impact && <span className="act-impact">{s.impact}</span>}
        <span className="act-conf" title={`confidence ${Math.round(s.confidence * 100)}%`}>{Math.round(s.confidence * 100)}%</span>
      </header>
      <p className="act-sug-detail">{s.detail}</p>
      {s.why && <p className="act-sug-why"><b>Because</b> {s.why}</p>}
      {s.evidence.length > 0 && (
        <div className="act-evidence">
          {s.evidence.map((id) => (
            <span key={id} className="act-tag" title={patterns[id]?.detail ?? id}>
              {PATTERN_ICON[patterns[id]?.kind ?? ''] ?? <Sparkles size={11} />}
              {patterns[id]?.title ?? id}
            </span>
          ))}
        </div>
      )}
      <footer>
        {s.status === 'new' && (
          <>
            <button className="primary-btn sm" onClick={onApply}>{act.icon} {act.label}</button>
            <button className="ghost-btn sm" onClick={() => onStatus('snoozed')} title="Hide it for a week">
              <BellOff size={13} /> Not now
            </button>
            <button className="ghost-btn sm danger" onClick={() => onStatus('dismissed')} title="Never suggest this again">
              <X size={13} /> Dismiss
            </button>
            <span className="muted small">effort: {s.effort}</span>
          </>
        )}
        {ruled && (
          <>
            <span className={`act-state-pill ${s.status}`}>{s.status}{s.status_note ? ` — ${s.status_note}` : ''}</span>
            <button className="link" onClick={() => onStatus('new')}>put it back</button>
          </>
        )}
      </footer>
    </article>
  )
}

function PatternRow({ p }: { p: ActivityPattern }): JSX.Element {
  return (
    <li className="act-pattern">
      <span className="act-pattern-icon">{PATTERN_ICON[p.kind] ?? <Sparkles size={13} />}</span>
      <span>
        <b>{p.title}</b>
        <small>{p.detail}</small>
      </span>
      <span className="act-conf" title={`seen on ${p.days} day(s)`}>{Math.round(p.confidence * 100)}%</span>
    </li>
  )
}

function EventRow({ e, onDelete }: { e: ActivityEvent; onDelete: () => void }): JSX.Element {
  const meta = e.meta as { keys?: number; clicks?: number; scrolls?: number; wpm?: number; secure_skipped?: number; channel?: string; since_seconds?: number }
  const detail = e.kind === 'input'
    ? `${meta.keys ?? 0} keys · ${meta.clicks ?? 0} clicks${meta.wpm ? ` · ${meta.wpm} wpm` : ''}${meta.secure_skipped ? ` · ${meta.secure_skipped} skipped (password field)` : ''}`
    : e.kind === 'idle' ? `away ${fmtDur((meta.since_seconds ?? 0) * 1000)}`
    : e.kind === 'audio' ? `${meta.channel ?? 'audio'} transcript`
    : e.title || e.url || '—'
  return (
    <tr className={e.rolled_up ? 'rolled' : ''}>
      <td className="act-t">{clockSec(e.ts)}</td>
      <td><span className={`act-kind ${e.kind}`}>{KIND_LABEL[e.kind] ?? e.kind}</span></td>
      <td className="act-app">{e.app || '—'}</td>
      <td className="act-detail" title={e.url || e.title}>{detail}</td>
      <td className="act-dur">{e.duration_ms ? fmtDur(e.duration_ms) : ''}</td>
      <td className="act-text" title={e.text}>{e.text || ''}</td>
      <td><button className="icon-btn ghost danger xs" title="Delete this row" onClick={onDelete}><Trash2 size={12} /></button></td>
    </tr>
  )
}

export default function ActivityView(): JSX.Element {
  const st = useStore((s) => s.activity)
  const events = useStore((s) => s.activityEvents)
  const summaries = useStore((s) => s.activitySummaries)
  const context = useStore((s) => s.activityContext)
  const insights = useStore((s) => s.activityInsights)
  const insightsBusy = useStore((s) => s.activityInsightsBusy)
  const busy = useStore((s) => s.activityBusy)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const {
    toggleSidebar, loadActivity, refreshActivity, setActivityConfig, toggleActivitySignal,
    startActivity, stopActivity, pauseActivity, resumeActivity, rollupActivity,
    refreshActivityProfile, deleteActivityEvent, deleteActivitySummary, purgeActivity,
    grantActivityPermission, openActivitySettings, setPalantirMode,
    loadActivityInsights, refreshActivityInsights, setInsightStatus, applyInsight, forgetActivityHabit
  } = useStore()
  const [tab, setTab] = useState<Tab>('overview')
  const [confirmPurge, setConfirmPurge] = useState<'events' | 'summaries' | 'all' | null>(null)
  const [showRuled, setShowRuled] = useState(false)
  const patternById = useMemo(
    () => Object.fromEntries(((insights as ActivityInsights | null)?.patterns ?? []).map((p) => [p.id, p])),
    [insights]
  )

  useEffect(() => { void loadActivity() }, [loadActivity])
  useEffect(() => { void loadActivityInsights() }, [loadActivityInsights])
  // Poll the live line while the panel is open; it is a cheap status read.
  useEffect(() => {
    const t = setInterval(() => void refreshActivity(), 5000)
    return () => clearInterval(t)
  }, [refreshActivity])

  const cfg = st?.config
  const live = Boolean(st?.running && !st.paused)
  const capById = useMemo(() => Object.fromEntries((st?.capabilities ?? []).map((c) => [c.id, c])), [st])

  if (!st || !cfg) {
    return (
      <main className="page">
        <header className="page-header drag">
          {!sidebarOpen && <button className="icon-btn no-drag" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
          <h2><MonitorDot size={16} /> Activity</h2>
          <AppSwitcher />
        </header>
        <div className="page-body"><p className="muted">Loading the activity monitor…</p></div>
      </main>
    )
  }

  /** A signal is blocked when the permission or tool it needs is missing. */
  const blocker = (s: ActivitySignal): string => {
    if (s === 'text' || s === 'input') {
      if (!capById.pyobjc?.ok) return capById.pyobjc?.fix ?? 'needs the native bridge'
      if (!capById.accessibility?.ok) return capById.accessibility?.fix ?? 'needs Accessibility permission'
    }
    if (s === 'apps' && !capById.pyobjc?.ok) return 'Window titles need the native bridge; app names still work.'
    if ((s === 'micAudio' || s === 'outputAudio') && !capById.ffmpeg?.ok) return capById.ffmpeg?.fix ?? 'needs ffmpeg'
    if (s === 'outputAudio' && !capById.loopback?.ok) return capById.loopback?.fix ?? 'needs a loopback device'
    return ''
  }

  const audioOn = cfg.signals.micAudio || cfg.signals.outputAudio

  return (
    <main className="page activity-page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><MonitorDot size={16} /> Activity</h2>
        <div className="no-drag header-right">
          {st.running && (
            st.paused
              ? <button className="ghost-btn" onClick={() => void resumeActivity()}><Play size={13} /> Resume</button>
              : <button className="ghost-btn" onClick={() => void pauseActivity(30)}><Pause size={13} /> Pause 30m</button>
          )}
          <button className="ghost-btn" disabled={busy} onClick={() => void rollupActivity()}>
            <RefreshCw size={13} className={busy ? 'spin' : ''} /> Summarize now
          </button>
          {st.running
            ? <button className="ghost-btn danger" onClick={() => void stopActivity()}><EyeOff size={14} /> Turn off</button>
            : <button className="primary-btn" disabled={!st.platform_supported} onClick={() => void startActivity()}><Eye size={14} /> Turn on</button>}
        </div>
        <AppSwitcher />
      </header>

      <div className="act-hero">
        <div className={`act-state ${live ? 'live' : st.paused ? 'paused' : 'off'}`}>
          <span className="act-dot" />
          {live ? 'Recording' : st.paused ? 'Paused' : 'Off'}
        </div>
        <p className="act-now">{st.now}</p>
        <div className="act-stats">
          <span>{st.counts.events} samples held</span>
          <span>{st.counts.summaries} summaries</span>
          <span>last rollup {ago(st.last_rollup)}</span>
          {st.secure_input && <span className="act-pill ok"><Shield size={11} /> password field focused — keystrokes dropped</span>}
          {st.palantir && <span className="act-pill warn"><Eye size={11} /> Palantir mode</span>}
        </div>
        {!st.platform_supported && <p className="act-warn"><AlertTriangle size={13} /> The collectors are macOS-only. Everything else in the app works normally.</p>}
        {st.last_error && <p className="act-warn"><AlertTriangle size={13} /> {st.last_error}</p>}
        {st.collectors.filter((c) => c.error).map((c) => (
          <p key={c.id} className="act-warn"><AlertTriangle size={13} /> <b>{c.id}</b>: {c.error}</p>
        ))}
        <div className="tabs">
          {TABS.map((t) => (
            <button key={t.key} className={tab === t.key ? 'active' : ''} onClick={() => setTab(t.key)}>
              {t.label}
              {t.key === 'log' && <span className="count">{events.length}</span>}
              {t.key === 'insights' && (insights?.counts.open ?? 0) > 0 && <span className="count">{insights?.counts.open}</span>}
            </button>
          ))}
        </div>
      </div>

      {tab === 'overview' && (
        <div className="page-body">
          <p className="muted small">
            The monitor watches what you do on this machine, summarizes it every {cfg.rollupMinutes} minutes, and
            writes the result to <code>{st.md_path}</code> — which is what gets fed into your chats. Raw samples are
            deleted after {cfg.retentionHours}h. Nothing is uploaded anywhere: the only network call is the
            summarization request to the LLM endpoint you already configured.
          </p>

          <Capabilities
            caps={st.capabilities}
            onGrant={(id, browser) => void grantActivityPermission(id, browser)}
            onOpen={(id) => void openActivitySettings(id)}
          />

          <PalantirCard
            on={st.palantir}
            missing={st.capabilities.filter((c) => c.state && c.state !== 'granted' && c.state !== 'n/a').map((c) => c.label)}
            onSet={(on) => void setPalantirMode(on)}
          />

          <section className="act-card">
            <div className="act-card-head static">
              <b>How it thinks you work</b>
              <span className="muted small">rebuilt every {cfg.profileEveryHours}h · updated {ago(st.profile_updated_at)}</span>
              <button className="link" disabled={busy} onClick={() => void refreshActivityProfile()}>rebuild now</button>
            </div>
            <div className="act-profile">
              {context?.markdown.includes('## How this person works')
                ? <Markdown remarkPlugins={[remarkGfm]}>{context.markdown.split('## How this person works')[1]?.split(/\n## /)[0] ?? ''}</Markdown>
                : <p className="muted">No profile yet. It appears once there are a few summaries to generalize from.</p>}
            </div>
          </section>

          <h4 className="act-h">Timeline</h4>
          {summaries.length === 0 && <p className="empty-hint">No summaries yet. Turn the monitor on, work for a few minutes, then hit “Summarize now”.</p>}
          <div className="act-timeline">
            {summaries.map((s) => (
              <article key={s.id} className="act-period">
                <header>
                  <time>{s.day} · {clock(s.period_start)}–{clock(s.period_end)}</time>
                  <b>{s.headline || 'Activity'}</b>
                  <button className="icon-btn ghost danger xs" title="Delete this summary" onClick={() => void deleteActivitySummary(s.id)}><Trash2 size={12} /></button>
                </header>
                <Markdown remarkPlugins={[remarkGfm]}>{s.body}</Markdown>
                {s.apps.length > 0 && <footer className="muted small">{s.apps.join(' · ')} — from {s.event_count} samples</footer>}
              </article>
            ))}
          </div>
        </div>
      )}

      {tab === 'insights' && (
        <div className="page-body">
          <p className="muted small">
            Patterns are mined from the same data, on this machine, with no model involved — they are the evidence,
            and they are what the panel shows you first. The suggestions on top of them are proposals: nothing here
            changes anything until you press a button, and dismissing one means it is never raised again.
          </p>

          <section className="act-card">
            <div className="act-card-head static">
              <b>What has been noticed</b>
              <span className="muted small">
                {insights?.window?.days
                  ? `${insights.window.days} day${insights.window.days === 1 ? '' : 's'} of history · ${insights.counts.days} day${insights.counts.days === 1 ? '' : 's'} aggregated · last pass ${ago(insights.last_run || null)}`
                  : 'nothing mined yet'}
              </span>
              <button className="link" disabled={insightsBusy} onClick={() => void refreshActivityInsights(false)}>re-read patterns</button>
              <button className="ghost-btn sm" disabled={insightsBusy} onClick={() => void refreshActivityInsights(true)}>
                <Sparkles size={13} className={insightsBusy ? 'spin' : ''} /> Find automations
              </button>
            </div>
            {insights?.last_error && <p className="act-warn"><AlertTriangle size={13} /> {insights.last_error}</p>}
            {insights && insights.patterns.length === 0 && (
              <p className="empty-hint">
                Not enough yet. A pattern has to recur on at least {cfg.insights.minDays} separate days before it
                counts, so give the monitor a couple of days of ordinary work — then press “Find automations”.
              </p>
            )}
            {insights && insights.patterns.length > 0 && (
              <>
                <ul className="act-patterns">
                  {insights.patterns.slice(0, 10).map((p) => <PatternRow key={p.id} p={p} />)}
                </ul>
                <div className="act-split">
                  <div>
                    <h5>Where the time goes</h5>
                    <Bars rows={insights.apps.slice(0, 6).map((a) => ({ label: a.app, value: a.seconds }))}
                      fmt={(v) => fmtDur(v * 1000)} />
                  </div>
                  <div>
                    <h5>Sites you keep opening</h5>
                    {insights.hosts.length === 0
                      ? <p className="muted small">Nothing — browser URLs are off, or no browser was sampled.</p>
                      : <Bars rows={insights.hosts.slice(0, 6).map((h) => ({ label: h.host, value: h.visits }))}
                          fmt={(v) => `${v}×`} />}
                  </div>
                </div>
                <h5>Hour of the day</h5>
                <HoursStrip hours={insights.hours} />
              </>
            )}
          </section>

          <h4 className="act-h">Habits <span className="muted small">— written into your memory, so chats already know them</span></h4>
          {!insights?.habits.length && (
            <p className="empty-hint">
              No habits yet. They are written once a pattern is confident enough, and each one owns a single row in
              the Memory panel — forgetting it here deletes that row too.
            </p>
          )}
          {!!insights?.habits.length && (
            <ul className="act-habits">
              {insights.habits.map((h) => (
                <HabitRow key={h.id} h={h} onForget={() => void forgetActivityHabit(h.id)} />
              ))}
            </ul>
          )}

          <h4 className="act-h">
            Suggestions
            {(insights?.counts.open ?? 0) > 0 && <span className="act-pill">{insights?.counts.open} waiting</span>}
          </h4>
          {!insights?.suggestions.filter((x) => x.status === 'new').length && (
            <p className="empty-hint">
              Nothing on offer. Press “Find automations” to look again — it reads the patterns above, not your
              screen.
            </p>
          )}
          <div className="act-sugs">
            {insights?.suggestions.filter((x) => x.status === 'new').map((x) => (
              <SuggestionCard key={x.id} s={x} patterns={patternById}
                onApply={() => void applyInsight(x.id)}
                onStatus={(status) => void setInsightStatus(x.id, status)} />
            ))}
          </div>

          {!!insights?.suggestions.filter((x) => x.status !== 'new').length && (
            <>
              <button className="link" onClick={() => setShowRuled((v) => !v)}>
                {showRuled ? 'hide' : 'show'} {insights.suggestions.filter((x) => x.status !== 'new').length} already
                decided
              </button>
              {showRuled && (
                <div className="act-sugs">
                  {insights.suggestions.filter((x) => x.status !== 'new').map((x) => (
                    <SuggestionCard key={x.id} s={x} patterns={patternById}
                      onApply={() => void applyInsight(x.id)}
                      onStatus={(status) => void setInsightStatus(x.id, status)} />
                  ))}
                </div>
              )}
            </>
          )}

          <h4 className="act-h">How this runs</h4>
          <label className="toggle-row">
            <span className="toggle-icon"><Sparkles size={15} /></span>
            <span className="toggle-text">
              <b>Look for habits and automations on a schedule</b>
              <small>One extra model call every {cfg.insights.everyHours}h, on the summaries and aggregates that are
                already stored. Off means the panel only looks when you press the button.</small>
            </span>
            <input type="checkbox" checked={cfg.insights.enabled}
              onChange={() => void setActivityConfig({ insights: { ...cfg.insights, enabled: !cfg.insights.enabled } })} />
            <span className="switch" />
          </label>
          <label className="toggle-row">
            <span className="toggle-icon"><Brain size={15} /></span>
            <span className="toggle-text">
              <b>Write confident habits into memory</b>
              <small>Each habit becomes one memory, visible and deletable in the Memory panel. Off leaves them here
                only, and deletes the ones already written on the next pass.</small>
            </span>
            <input type="checkbox" checked={cfg.insights.autoMemory}
              onChange={() => void setActivityConfig({ insights: { ...cfg.insights, autoMemory: !cfg.insights.autoMemory } })} />
            <span className="switch" />
          </label>
          <NumberField label="Look again every" hint="0 only ever looks when you ask" value={cfg.insights.everyHours}
            min={0} max={168} suffix="hours"
            onCommit={(v) => void setActivityConfig({ insights: { ...cfg.insights, everyHours: v } })} />
          <NumberField label="History to mine" hint="How many days of aggregates a pattern may draw on"
            value={cfg.insights.lookbackDays} min={2} max={90} suffix="days"
            onCommit={(v) => void setActivityConfig({ insights: { ...cfg.insights, lookbackDays: v } })} />
          <NumberField label="Recurrence floor" hint="Days a pattern must appear on before it counts"
            value={cfg.insights.minDays} min={1} max={14} suffix="days"
            onCommit={(v) => void setActivityConfig({ insights: { ...cfg.insights, minDays: v } })} />
          <NumberField label="Suggestions at a time" hint="Fewer and better beats a long list"
            value={cfg.insights.maxSuggestions} min={1} max={20} suffix="items"
            onCommit={(v) => void setActivityConfig({ insights: { ...cfg.insights, maxSuggestions: v } })} />
        </div>
      )}

      {tab === 'signals' && (
        <div className="page-body">
          <p className="muted small">
            Each signal is separate and off until you switch it on. Read what a signal captures before enabling it —
            the heavier ones are marked, and they mean exactly what they say.
          </p>
          {st.palantir && (
            <p className="act-warn">
              <AlertTriangle size={13} /> Palantir mode has every signal on. Turning one off here leaves the mode on;
              turn the mode off on the Overview tab to restore the signals you had before.
            </p>
          )}
          <div className="act-signals">
            {(Object.keys(SIGNAL_INFO) as ActivitySignal[]).map((s) => {
              const info = SIGNAL_INFO[s]
              const why = blocker(s)
              const on = cfg.signals[s]
              return (
                <div key={s} className={`act-signal ${info.heavy ? 'heavy' : ''} ${on ? 'on' : ''}`}>
                  <label className="toggle-row">
                    <span className="toggle-icon">{info.icon}</span>
                    <span className="toggle-text">
                      <b>{info.label}{info.heavy && <span className="act-pill warn">sensitive</span>}</b>
                      <small>{info.what}</small>
                    </span>
                    <input type="checkbox" checked={on} onChange={() => void toggleActivitySignal(s)} />
                    <span className="switch" />
                  </label>
                  {why && <p className="act-fix"><AlertTriangle size={12} /> {why}</p>}
                </div>
              )
            })}
          </div>

          <h4 className="act-h">Sampling</h4>
          <NumberField label="Sample interval" hint="How often the frontmost window is checked" value={cfg.sampleSeconds} min={1} max={120} suffix="seconds" onCommit={(v) => void setActivityConfig({ sampleSeconds: v })} />
          <NumberField label="Idle after" hint="No input for this long counts as away" value={cfg.idleSeconds} min={30} max={3600} step={30} suffix="seconds" onCommit={(v) => void setActivityConfig({ idleSeconds: v })} />
          <NumberField label="Summarize every" hint="How often raw samples are turned into prose" value={cfg.rollupMinutes} min={5} max={180} step={5} suffix="minutes" onCommit={(v) => void setActivityConfig({ rollupMinutes: v })} />
          <NumberField label="Rebuild profile every" hint="0 disables the durable profile entirely" value={cfg.profileEveryHours} min={0} max={168} suffix="hours" onCommit={(v) => void setActivityConfig({ profileEveryHours: v })} />

          {audioOn && (
            <>
              <h4 className="act-h">Audio</h4>
              <p className="muted small">
                Recordings are transcribed and then deleted; only text is stored. Transcription goes to
                <code> {cfg.audio.model}</code> on your configured base URL.
              </p>
              <label className="act-field">
                <span><b>Microphone device</b><small>ffmpeg avfoundation input</small></span>
                <select value={cfg.audio.micDevice} onChange={(e) => void setActivityConfig({ audio: { ...cfg.audio, micDevice: e.target.value } })}>
                  <option value="">(none)</option>
                  {(st.audio_devices.length ? st.audio_devices : []).map((d) => <option key={d.index} value={d.index}>[{d.index}] {d.name}</option>)}
                </select>
              </label>
              <label className="act-field">
                <span><b>System audio device</b><small>must be a loopback device such as BlackHole</small></span>
                <select value={cfg.audio.outputDevice} onChange={(e) => void setActivityConfig({ audio: { ...cfg.audio, outputDevice: e.target.value } })}>
                  <option value="">(none)</option>
                  {st.audio_devices.map((d) => <option key={d.index} value={d.index}>[{d.index}] {d.name}</option>)}
                </select>
              </label>
              <label className="act-field">
                <span><b>Transcription model</b><small>any speech-to-text model your proxy exposes</small></span>
                <input value={cfg.audio.model} onChange={(e) => void setActivityConfig({ audio: { ...cfg.audio, model: e.target.value } })} />
              </label>
              <NumberField label="Chunk length" hint="Longer chunks transcribe better and cost fewer calls" value={cfg.audio.chunkSeconds} min={5} max={300} step={5} suffix="seconds" onCommit={(v) => void setActivityConfig({ audio: { ...cfg.audio, chunkSeconds: v } })} />
              {st.audio_devices.length === 0 && <p className="act-fix"><AlertTriangle size={12} /> No audio inputs visible. Grant Microphone permission to the app and reopen this panel.</p>}
            </>
          )}
        </div>
      )}

      {tab === 'privacy' && (
        <div className="page-body">
          {st.palantir && (
            <p className="act-warn">
              <AlertTriangle size={13} /> Palantir mode is on: redaction is off and both “never record” lists are
              empty. Editing them here leaves the mode on — turn it off on the Overview tab to get your previous
              settings back.
            </p>
          )}
          <label className="toggle-row plain">
            <span className="toggle-icon"><Shield size={15} /></span>
            <span className="toggle-text"><b>Redact before storing</b><small>Strips emails, phone numbers, card numbers, SSNs, API keys, JWTs and private keys out of typed text and transcripts, and throws away whatever follows a word like “password” or “seed phrase”. Leave this on.</small></span>
            <input type="checkbox" checked={cfg.redact} onChange={(e) => void setActivityConfig({ redact: e.target.checked })} />
            <span className="switch" />
          </label>
          <label className="toggle-row plain">
            <span className="toggle-icon"><Sparkles size={15} /></span>
            <span className="toggle-text"><b>Feed summaries into chats</b><small>Off keeps recording and summarizing, but no chat sees any of it. Individual chats can also opt out in the Context drawer.</small></span>
            <input type="checkbox" checked={cfg.injectContext} onChange={(e) => void setActivityConfig({ injectContext: e.target.checked })} />
            <span className="switch" />
          </label>

          <h4 className="act-h">Retention</h4>
          <NumberField label="Keep raw samples" hint="Deleted automatically once past this age" value={cfg.retentionHours} min={1} max={720} suffix="hours" onCommit={(v) => void setActivityConfig({ retentionHours: v })} />
          <NumberField label="Keep summaries" hint="The prose written to activity.md" value={cfg.summaryRetentionDays} min={1} max={730} suffix="days" onCommit={(v) => void setActivityConfig({ summaryRetentionDays: v })} />
          <NumberField label="Detail in activity.md" hint="Older days drop out of the file but stay in the database" value={cfg.contextDays} min={1} max={30} suffix="days" onCommit={(v) => void setActivityConfig({ contextDays: v })} />

          <h4 className="act-h">Never record</h4>
          <ListEditor
            label="Excluded apps" items={cfg.excludeApps} placeholder="App name, e.g. Signal"
            hint="Matched against the app name. While one of these is in front, nothing is recorded — the timeline just shows “(private)” for that stretch."
            onChange={(excludeApps) => void setActivityConfig({ excludeApps })}
          />
          <ListEditor
            label="Excluded window titles and URLs" items={cfg.excludeTitlePatterns} placeholder="Substring, e.g. payroll"
            hint="Case-insensitive substring match against the window title and the URL. A match skips that window entirely."
            onChange={(excludeTitlePatterns) => void setActivityConfig({ excludeTitlePatterns })}
          />

          <h4 className="act-h">Delete</h4>
          <p className="muted small">Deleting is immediate and cannot be undone.</p>
          <div className="act-danger">
            {(['events', 'summaries', 'all'] as const).map((s) => (
              <button key={s} className="ghost-btn danger" onClick={() => setConfirmPurge(s)}>
                <Trash2 size={13} /> {s === 'events' ? 'Delete raw samples' : s === 'summaries' ? 'Delete summaries' : 'Delete everything'}
              </button>
            ))}
          </div>
          {confirmPurge && (
            <div className="modal-backdrop" onMouseDown={() => setConfirmPurge(null)}>
              <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
                <header><h2>Delete {confirmPurge === 'all' ? 'everything the monitor knows' : confirmPurge === 'events' ? 'all raw samples' : 'all summaries'}?</h2></header>
                <p className="muted">
                  {confirmPurge === 'all'
                    ? 'Every sample, every summary and the work profile. activity.md is rewritten empty.'
                    : confirmPurge === 'events'
                    ? 'The raw sample log. Summaries already written stay.'
                    : 'Every summary and the contents of activity.md. Raw samples stay.'}
                </p>
                <footer>
                  <button className="ghost-btn" onClick={() => setConfirmPurge(null)}>Cancel</button>
                  <button className="primary-btn danger" onClick={() => { void purgeActivity(confirmPurge); setConfirmPurge(null) }}>Delete</button>
                </footer>
              </div>
            </div>
          )}
        </div>
      )}

      {tab === 'context' && (
        <div className="page-body">
          <p className="muted small">
            This is the file the monitor maintains, at <code>{st.md_path}</code>. It is plain markdown — read it,
            back it up, or delete it.
          </p>
          <h4 className="act-h">What chats actually receive</h4>
          {context?.injected
            ? <pre className="act-injected">{context.injected}</pre>
            : <p className="empty-hint">Nothing is being injected right now{cfg.injectContext ? '' : ' (injection is off in Privacy)'}.</p>}
          <h4 className="act-h">activity.md</h4>
          <div className="act-md">
            {context?.markdown
              ? <Markdown remarkPlugins={[remarkGfm]}>{context.markdown}</Markdown>
              : <p className="muted">The file has not been written yet.</p>}
          </div>
        </div>
      )}

      {tab === 'log' && (
        <div className="page-body">
          <p className="muted small">
            Every sample held right now, newest first — the complete record, before summarization. Greyed rows have
            already been folded into a summary. Delete any row you would rather the monitor had not seen.
          </p>
          {events.length === 0 && <p className="empty-hint">Nothing recorded in the last 24 hours.</p>}
          {events.length > 0 && (
            <div className="act-log">
              <table>
                <thead><tr><th>Time</th><th>Kind</th><th>App</th><th>Detail</th><th>For</th><th>Text captured</th><th /></tr></thead>
                <tbody>
                  {events.map((e) => <EventRow key={e.id} e={e} onDelete={() => void deleteActivityEvent(e.id)} />)}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </main>
  )
}

/** Small live indicator for the sidebar, so recording is never invisible. */
export function ActivityIndicator(): JSX.Element | null {
  const st = useStore((s) => s.activity) as ActivityStatus | null
  const setView = useStore((s) => s.setView)
  if (!st?.running) return null
  return (
    <button className={`act-indicator ${st.paused ? 'paused' : 'live'}`} onClick={() => setView('activity')}
      title={st.paused ? 'Activity monitor paused' : st.now}>
      <span className="act-dot" />
      {st.paused ? 'Paused' : 'Recording'}
    </button>
  )
}
