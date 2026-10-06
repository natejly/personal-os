import { useCallback, useEffect, useRef, useState } from 'react'
import { buildRows, grantAllPlan, stateLabel, type AccessState, type BackendAccess, type MainStatus, type ShellCheck, type Cli } from '@shared/systemAccess'
import { api } from '../lib/api'
import './PermissionsPanel.css'

const UNKNOWN: MainStatus = { microphone: 'unknown', camera: 'unknown', screen: 'unknown', accessibility: 'unknown', notifications: 'unknown' }

function CliLine({ name, cli }: { name: string; cli: Cli }): JSX.Element {
  return <li><b>{name}</b>: {cli.path ? <><code>{cli.path}</code>{cli.version ? ` (${cli.version})` : ''}</> : <>Not found. {cli.hint}</>}</li>
}

/** Every macOS grant Grain can use, one row each. Reads never prompt; only a Grant button does. */
export function PermissionsPanel({ compact }: { compact?: boolean }): JSX.Element {
  const sys = window.os?.sysAccess
  const [main, setMain] = useState<MainStatus>(UNKNOWN)
  const [backend, setBackend] = useState<BackendAccess | null>(null)
  const [shell, setShell] = useState<ShellCheck | null>(null)
  const [probed, setProbed] = useState<Record<string, AccessState>>({})
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [progress, setProgress] = useState('')
  const [busy, setBusy] = useState(false)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  const refreshStatus = useCallback(async (): Promise<void> => {
    const [m, b] = await Promise.all([
      sys ? sys.status().catch(() => null) : Promise.resolve(null),
      api.system.access().catch(() => null)
    ])
    if (!alive.current) return
    setMain(m ?? UNKNOWN)
    setBackend(b)
  }, [sys])
  const refresh = useCallback(async (): Promise<void> => {
    await refreshStatus()
    void api.system.shellCheck().then((r) => alive.current && setShell(r)).catch(() => alive.current && setShell({ ok: false, output: '', cwd: null, error: 'The backend did not answer.' }))
  }, [refreshStatus])
  useEffect(() => {
    void refresh()
    const f = (): void => void refreshStatus()
    window.addEventListener('focus', f)
    return () => window.removeEventListener('focus', f)
  }, [refresh, refreshStatus])

  const rows = buildRows(main, backend, probed)
  const grant = async (id: string): Promise<void> => {
    const row = rows.find((r) => r.id === id)
    if (!row) return
    try {
      if (id === 'inputMonitoring') await api.activity.requestPermission('input_monitoring')
      else if (id === 'fullDisk') await sys?.openPane(row.pane ?? '')
      else if (sys) {
        const r = await sys.grant(id)
        if (!alive.current) return
        setProbed((p) => ({ ...p, [id]: r.state }))
        setNotes((n) => ({ ...n, [id]: r.note ?? '' }))
      }
    } catch (e) {
      setNotes((n) => ({ ...n, [id]: e instanceof Error ? e.message : 'Could not ask.' }))
    }
    if (alive.current) await refreshStatus()
  }
  const grantAll = async (): Promise<void> => {
    setBusy(true)
    for (const id of grantAllPlan(rows)) {
      if (!alive.current) return
      setProgress(`Asking for ${rows.find((r) => r.id === id)?.label ?? id}…`)
      await grant(id)
    }
    if (!alive.current) return
    setProgress('')
    setBusy(false)
  }

  const noElectron = !sys
  return (
    <div className={`perm-panel${compact ? ' compact' : ''}`}>
      <div className="perm-bar">
        <button type="button" className="ghost-btn" disabled={busy || noElectron} onClick={() => void grantAll()}>Grant all</button>
        <button type="button" className="ghost-btn" disabled={busy} onClick={() => void refresh()}>Re-check</button>
        <span className="muted small" role="status" aria-live="polite">{progress}</span>
      </div>
      {noElectron && <p className="muted small">Not running in the desktop app, so statuses cannot be read.</p>}
      <ul className="plain-list">
        {rows.map((r) => (
          <li key={r.id} className="perm-row">
            <span className="perm-main">
              <span className="perm-label">{r.label}</span>
              <span className="perm-reason">{r.reason}{notes[r.id] ? ` ${notes[r.id]}` : ''}</span>
            </span>
            <span className={`perm-badge ${r.state}`}>{stateLabel(r.state)}</span>
            {r.state !== 'granted' && (
              <button type="button" className="ghost-btn" disabled={busy || noElectron} aria-label={`Grant ${r.label}`} onClick={() => void grant(r.id)}>Grant</button>
            )}
          </li>
        ))}
      </ul>

      <h4>Terminal</h4>
      <p className="small">{shell === null ? 'Checking the agent\'s shell…' : shell.ok ? `The agent's shell runs (cwd ${shell.cwd ?? 'unknown'}).` : `The agent's shell failed: ${shell.error ?? 'unknown error'}`}</p>
      <h4>Where Grain works</h4>
      <p className="small">Grain can read and write anywhere on this Mac, in every permission mode. Two kinds of place are held back.</p>
      <p className="small"><b>Off limits</b> (never, in any mode): {backend?.scope?.protected.length ? backend.scope.protected.join(', ') : 'its own data folder and the Grain app'}.</p>
      <p className="small"><b>Always asks first</b> (reads and writes, in every mode): {backend?.scope?.sensitive.length ? backend.scope.sensitive.join(', ') : 'passwords, keys and sign-in files'}.</p>
      {backend && <ul className="small"><CliLine name="claude" cli={backend.clis.claude} /><CliLine name="opencode" cli={backend.clis.opencode} /></ul>}

      <p className="muted small">Some grants only take effect after Grain restarts. Rows marked Unknown are ones macOS gives no way to check; Grant opens the right pane.</p>
    </div>
  )
}
