import { useEffect, useMemo, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  AlertTriangle, Check, ChevronRight, Eye, EyeOff, FileText, Globe, Keyboard, MonitorDot,
  Mic, PanelLeftOpen, Pause, Play, RefreshCw, Shield, Speaker, Sparkles, Trash2, X
} from 'lucide-react'
import { useStore } from '../store'
import type { ActivityCapability, ActivityEvent, ActivitySignal, ActivityStatus } from '@shared/types'

/** The activity monitor: what it records, what it inferred, and every switch that turns it off.
 *
 *  The panel is deliberately blunt about cost to privacy. Each signal says in plain words what it
 *  captures before you can turn it on, the raw log is browsable and row-deletable, and the purge
 *  controls are one click away - because the only way this feature is reasonable to ship is if the
 *  person running it can always see exactly what it knows. */

const TABS = [
  { key: 'overview', label: 'Overview' },
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

function Capabilities({ caps }: { caps: ActivityCapability[] }): JSX.Element {
  const bad = caps.filter((c) => !c.ok)
  const [open, setOpen] = useState(bad.length > 0)
  return (
    <section className="act-card">
      <button className="act-card-head" onClick={() => setOpen((o) => !o)}>
        <ChevronRight size={13} className={open ? 'rot90' : ''} />
        <b>What this machine can do</b>
        <span className={`act-pill ${bad.length ? 'warn' : 'ok'}`}>{bad.length ? `${bad.length} need attention` : 'all available'}</span>
      </button>
      {open && (
        <ul className="act-caps">
          {caps.map((c) => (
            <li key={c.id} className={c.ok ? 'ok' : 'bad'}>
              {c.ok ? <Check size={13} /> : <AlertTriangle size={13} />}
              <div>
                <b>{c.label}</b>
                <p>{c.detail}</p>
                {!c.ok && c.fix && <p className="act-fix">{c.fix}</p>}
              </div>
            </li>
          ))}
        </ul>
      )}
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

/**
 * The mic and system-audio device pair, exported because the meetings panel picks the same two
 * devices. One picker means one place where the loopback caveat is written down, and no chance of
 * the two panels disagreeing about what an empty value means.
 *
 * `devices` is deliberately the narrower `{index, name}` shape: `/meetings/status` adds a
 * `loopback` flag to its rows and `/activity/devices` does not, so the common shape is this one.
 */
export function AudioDevicePicker({ devices, micValue, outputValue, onChange }: {
  devices: { index: string; name: string }[]
  micValue: string
  outputValue: string
  onChange: (patch: { micDevice?: string; outputDevice?: string }) => void
}): JSX.Element {
  const options = devices.map((d) => <option key={d.index} value={d.index}>[{d.index}] {d.name}</option>)
  return (
    <>
      <label className="act-field">
        <span><b>Microphone device</b><small>ffmpeg avfoundation input</small></span>
        <select value={micValue} onChange={(e) => onChange({ micDevice: e.target.value })}>
          <option value="">(none)</option>
          {options}
        </select>
      </label>
      <label className="act-field">
        <span><b>System audio device</b><small>must be a loopback device such as BlackHole</small></span>
        <select value={outputValue} onChange={(e) => onChange({ outputDevice: e.target.value })}>
          <option value="">(none)</option>
          {options}
        </select>
      </label>
    </>
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
  const busy = useStore((s) => s.activityBusy)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const {
    toggleSidebar, loadActivity, refreshActivity, setActivityConfig, toggleActivitySignal,
    startActivity, stopActivity, pauseActivity, resumeActivity, rollupActivity,
    refreshActivityProfile, deleteActivityEvent, deleteActivitySummary, purgeActivity
  } = useStore()
  const [tab, setTab] = useState<Tab>('overview')
  const [confirmPurge, setConfirmPurge] = useState<'events' | 'summaries' | 'all' | null>(null)

  useEffect(() => { void loadActivity() }, [loadActivity])
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

          <Capabilities caps={st.capabilities} />

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

      {tab === 'signals' && (
        <div className="page-body">
          <p className="muted small">
            Each signal is separate and off until you switch it on. Read what a signal captures before enabling it —
            the heavier ones are marked, and they mean exactly what they say.
          </p>
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
              <AudioDevicePicker
                devices={st.audio_devices} micValue={cfg.audio.micDevice} outputValue={cfg.audio.outputDevice}
                onChange={(p) => void setActivityConfig({ audio: { ...cfg.audio, ...p } })}
              />
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
