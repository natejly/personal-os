/**
 * Connected services: fitness accounts whose daily numbers flow into the health log through an MCP
 * server. Each one goes connect → (sign in, for a remote server) → approve its tools → sync.
 * Approval is per tool shape: if the server later changes a tool, sync skips it and this panel says so.
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Check, ChevronDown, ChevronRight, ExternalLink, Link2, RefreshCw, Trash2 } from 'lucide-react'
import type { HealthProvider, HealthSource, HealthSourcePlan, HealthSyncResult } from '@shared/types'
import { useStore } from '../../store'
import { api } from '../../lib/api'
import { localDay } from '../../components/CalendarWeek'
import { signInMcp } from '../../components/McpSettings'

const ago = (t: number | null): string => {
  if (!t) return 'never'
  const s = Date.now() / 1000 - t
  if (s < 90) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return new Date(t * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

const summary = (r: HealthSyncResult | Record<string, never>): string => {
  if (!('written' in r)) return ''
  const w = Object.entries(r.written).map(([k, n]) => `${n} ${k.replace('_', ' ')}`)
  return w.length ? `Updated ${w.join(', ')}` : r.unchanged ? 'Already up to date' : 'Nothing new'
}

export default function Sources({ onSynced, onClose }: { onSynced: () => Promise<void>; onClose: () => void }): JSX.Element {
  const toast = useStore((s) => s.toast)
  const [providers, setProviders] = useState<HealthProvider[]>([])
  const [sources, setSources] = useState<HealthSource[] | null>(null)
  const reload = useCallback(async () => setSources(await api.healthLog.sources().catch(() => [])), [])
  useEffect(() => {
    void api.healthLog.providers().then(setProviders).catch(() => setProviders([]))
    void reload()
  }, [reload])
  // A server that is still starting (uvx fetching Garmin's package, a fresh sign-in reconnecting)
  // has no tools yet; keep the list fresh until everything has settled.
  const settling = sources?.some((s) => s.server && (s.server.status === 'connecting' || s.server.status === 'idle'))
  useEffect(() => {
    if (!settling) return
    const t = setInterval(() => void reload(), 2500)
    return () => clearInterval(t)
  }, [settling, reload])

  const connected = new Set(sources?.map((s) => s.provider))
  return (
    <section className="hl-manage hl-sources" aria-label="Connected services">
      <header className="hl-manage-head">
        <h3>Connected services</h3>
        <span className="muted small">Sleep, steps, heart rate, workouts and weight sync in each day. If you also log a day yourself, the higher total counts, never both.</span>
        <button className="ghost-btn" onClick={onClose}>Done</button>
      </header>
      {sources?.map((s) => <SourceRow key={s.id} s={s} reload={reload} onSynced={onSynced} toast={toast} />)}
      <div className="hl-providers">
        {providers.filter((p) => !connected.has(p.key)).map((p) => <Connect key={p.key} p={p} onDone={reload} toast={toast} />)}
      </div>
      <p className="muted small hl-src-note">
        Strava isn&apos;t offered: its API terms don&apos;t allow its data to be used by AI apps, and the assistant here reads your health log.
      </p>
    </section>
  )
}

type Toast = (msg: string, kind?: 'error' | 'info') => void

function Connect({ p, onDone, toast }: { p: HealthProvider; onDone: () => Promise<void>; toast: Toast }): JSX.Element {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState<Record<string, string>>(() => Object.fromEntries(p.needs.map((n) => [n.key, n.default ?? ''])))
  const [busy, setBusy] = useState(false)
  const [cmd, ...rest] = p.setup.split('\n').reverse()
  const command = p.setup.includes('\n') ? cmd : ''
  const prose = p.setup.includes('\n') ? rest.reverse().join(' ') : p.setup
  const go = async (): Promise<void> => {
    setBusy(true)
    try {
      await api.healthLog.connect(p.key, form)
      await onDone()
      setOpen(false)
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className={`hl-provider ${open ? 'open' : ''}`}>
      <button className="hl-provider-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <strong>{p.label}</strong>
        <span className="muted small">{p.metrics.map((m) => m.replace('_', ' ')).join(', ')}</span>
      </button>
      {open && (
        <form className="hl-provider-body" onSubmit={(e) => { e.preventDefault(); void go() }}>
          <p className="muted small">{prose}</p>
          {command && <pre className="hl-cmd" title="Run once in a terminal"><code>{command}</code></pre>}
          {p.needs.map((n) => (
            <label key={n.key} className="hl-field">
              <span>{n.label}</span>
              {n.options
                ? <select value={form[n.key]} onChange={(e) => setForm({ ...form, [n.key]: e.target.value })}>{n.options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
                : <input type={n.secret ? 'password' : 'text'} autoComplete="off" value={form[n.key]} onChange={(e) => setForm({ ...form, [n.key]: e.target.value })} className="hl-in" />}
            </label>
          ))}
          <button type="submit" className="primary-btn" disabled={busy}><Link2 size={14} /> {busy ? 'Connecting…' : `Connect ${p.label}`}</button>
        </form>
      )}
    </div>
  )
}

function SourceRow({ s, reload, onSynced, toast }: { s: HealthSource; reload: () => Promise<void>; onSynced: () => Promise<void>; toast: Toast }): JSX.Element {
  const [plan, setPlan] = useState<HealthSourcePlan | null>(null)
  const [signing, setSigning] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [showProblems, setShowProblems] = useState(false)

  const status = s.server?.status ?? 'missing'
  const remote = s.server?.transport === 'http'
  const needsSignIn = remote && (s.server?.detail === 'sign-in required' || (status !== 'ready' && status !== 'connecting'))
  const approved = Object.keys(s.pinned).length > 0
  const loadPlan = useCallback(async () => setPlan(await api.healthLog.plan(s.id).catch(() => null)), [s.id])
  useEffect(() => { if (status === 'ready') void loadPlan() }, [status, loadPlan])
  const changed = plan?.tools.some((t) => t.changed)
  const unapproved = plan ? plan.tools.some((t) => t.tool && !t.pinned) : !approved

  const signIn = async (): Promise<void> => {
    if (!s.server) return
    setSigning(true)
    try {
      const x = await signInMcp(s.server.id)
      if (x.status === 'error') toast(`${s.label} sign-in failed: ${x.error}`, 'error')
      await reload()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setSigning(false)
    }
  }
  const approve = async (): Promise<void> => {
    try {
      setPlan(await api.healthLog.approve(s.id))
      await reload()
    } catch (e) {
      return toast((e as Error).message, 'error')
    }
    await sync()
  }
  const sync = async (): Promise<void> => {
    setSyncing(true)
    try {
      const r = await api.healthLog.sync(s.id, localDay())
      toast(`${s.label}: ${summary(r).toLowerCase()}${r.problems.length ? ` · ${r.problems.length} problem${r.problems.length === 1 ? '' : 's'}` : ''}`)
      await Promise.all([reload(), onSynced()])
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setSyncing(false)
    }
  }
  const remove = async (): Promise<void> => {
    if (!window.confirm(`Disconnect ${s.label}? Readings it already synced stay in your log.`)) return
    try {
      await api.healthLog.disconnect(s.id, true)
      await reload()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const problems = 'problems' in s.last_result ? s.last_result.problems : []
  const state = !s.server ? 'Connector removed'
    : needsSignIn ? (signing ? 'Waiting for you to sign in in the browser…' : 'Sign in to finish connecting')
      : status === 'connecting' || status === 'idle' ? 'Starting…'
        : status === 'error' ? `Not connected: ${s.server.detail}`
          : changed ? 'Some tools changed since you approved them'
            : unapproved ? 'Review what it may read, then approve'
              : `Last sync ${ago(s.last_sync_at)}${s.last_sync_at ? ` · ${summary(s.last_result)}` : ''}`

  return (
    <div className="hl-source">
      <div className="hl-source-head">
        <span className={`hl-dotstate ${status === 'ready' && !unapproved && !changed ? 'ok' : status === 'error' || changed ? 'bad' : ''}`} aria-hidden />
        <strong>{s.label}</strong>
        <span className="muted small hl-source-state">{state}</span>
        <span className="hl-mrow-actions">
          {needsSignIn && s.server && <button className="primary-btn" disabled={signing} onClick={() => void signIn()}><ExternalLink size={13} /> {signing ? 'Signing in…' : 'Sign in'}</button>}
          {status === 'ready' && !unapproved && !changed && (
            <button className="ghost-btn" disabled={syncing} onClick={() => void sync()} title="Pull the last few days now">
              <RefreshCw size={13} className={syncing ? 'spin' : ''} /> Sync now
            </button>
          )}
          <label className="check small" title="Sync automatically every few hours">
            <input type="checkbox" checked={s.enabled} onChange={(e) => void api.healthLog.updateSource(s.id, { enabled: e.target.checked }).then(reload)} /> Auto
          </label>
          <button className="icon-btn" aria-label={`Disconnect ${s.label}`} title="Disconnect" onClick={() => void remove()}><Trash2 size={13} /></button>
        </span>
      </div>
      {status === 'ready' && plan && (unapproved || changed) && (
        <div className="hl-plan">
          <p className="muted small">Sync will call only these read tools on your {s.label} account, and only parses numbers and dates from their answers:</p>
          <ul>
            {plan.tools.map((t) => (
              <li key={t.wants[0]} className={t.tool ? '' : 'off'}>
                <code>{t.tool ?? t.wants[0]}</code>
                <span className="muted small"> → {t.metrics.map((m) => m.replace('_', ' ')).join(', ')}</span>
                {!t.tool && <span className="muted small"> (not offered by this server)</span>}
                {t.changed && <span className="hl-warn small"><AlertTriangle size={12} /> changed</span>}
              </li>
            ))}
          </ul>
          <button className="primary-btn" onClick={() => void approve()} disabled={!plan.tools.some((t) => t.tool)}><Check size={14} /> Approve and sync</button>
        </div>
      )}
      {problems.length > 0 && !unapproved && (
        <div className="hl-problems">
          <button className="link small" onClick={() => setShowProblems((v) => !v)}>{showProblems ? 'Hide' : 'Show'} {problems.length} problem{problems.length === 1 ? '' : 's'} from the last sync</button>
          {showProblems && (
            <ul>
              {problems.map((p, i) => (
                <li key={i}><code>{p.tool}</code>: {p.error}{p.sample && <pre className="hl-cmd"><code>{p.sample}</code></pre>}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
