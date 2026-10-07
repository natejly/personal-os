import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, ExternalLink, Loader2, X } from 'lucide-react'
import { api } from '../../lib/api'
import type { McpCatalogEntry, McpServer } from '@shared/types'
import { POLL_MS, fieldsValid, initialValues, needsSignIn } from './catalog'
import { signInMcp } from './signIn'

/** How many stderr lines to show under a failed connector. */
const LOG_TAIL = 12

/** The form an entry's `fields` describe. Secret values are only ever typed here and sent once. */
export function InstallForm({ entry, busy, error, onSubmit, onCancel }: {
  entry: McpCatalogEntry; busy: boolean; error: string
  onSubmit: (values: Record<string, string>) => void; onCancel: () => void
}): JSX.Element {
  const [values, setValues] = useState(() => initialValues(entry))
  const [tried, setTried] = useState(false)
  const missing = fieldsValid(entry, values)
  const submit = (): void => {
    setTried(true)
    if (!missing.length) onSubmit(values)
  }
  return (
    <form className="mcp-server open mcp-add" onSubmit={(e) => { e.preventDefault(); submit() }}>
      <div className="mcp-head"><b>Install {entry.name}</b>
        <button type="button" className="icon-btn" aria-label="Cancel" onClick={onCancel}><X size={14} /></button>
      </div>
      <div className="mcp-body">
        {entry.fields.length === 0 && <p className="muted small">Nothing to fill in. It starts as soon as it is installed.</p>}
        {entry.fields.map((f) => {
          const common = {
            value: values[f.id] ?? '', placeholder: f.placeholder, spellCheck: false, required: !!f.required,
            'aria-invalid': tried && missing.includes(f.id), onChange: (e: { target: { value: string } }) => setValues({ ...values, [f.id]: e.target.value })
          }
          return (
            <label key={f.id}>
              <span>{f.label}{f.required && <b className="req" title="Required"> *</b>}{f.multiple && <small className="muted"> (one per line)</small>}</span>
              {f.multiple
                ? <textarea rows={3} {...common} />
                : <input {...common} type={f.secret ? 'password' : 'text'} autoComplete="off" />}
              {f.help && <small className="muted">{f.help}</small>}
              {f.secret && <small className="muted">Kept in the backend and never shown again.</small>}
            </label>
          )
        })}
        {tried && missing.length > 0 && <p className="test-msg fail">Fill in: {missing.map((id) => entry.fields.find((f) => f.id === id)?.label ?? id).join(', ')}</p>}
        {error && <p className="test-msg fail">{error}</p>}
        <div className="mcp-actions">
          <button type="submit" className="primary-btn" disabled={busy}>{busy ? 'Installing…' : 'Install'}</button>
          <button type="button" className="ghost-btn" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </form>
  )
}

/** Follows a freshly installed connector until it is ready, needs a browser sign-in, or fails. */
export function InstallStatus({ serverId, name, oauth, onClose }: {
  serverId: string; name: string; oauth: boolean; onClose: () => void
}): JSX.Element {
  const [server, setServer] = useState<McpServer | null>(null)
  const [stderr, setStderr] = useState<string[]>([])
  const [signErr, setSignErr] = useState('')
  const [signing, setSigning] = useState(false)
  const started = useRef(false)

  const poll = useCallback(async () => {
    try { setServer((await api.mcp.servers()).find((s) => s.id === serverId) ?? null) } catch { /* the next tick retries */ }
  }, [serverId])

  const signIn = useCallback(async () => {
    setSigning(true)
    setSignErr('')
    try {
      const r = await signInMcp(serverId)
      if (r.status === 'error') setSignErr(r.error)
    } catch (e) {
      setSignErr((e as Error).message)
    } finally {
      setSigning(false)
      void poll()
    }
  }, [serverId, poll])

  const signin = !!server && needsSignIn(server)
  const failed = !!server && server.live.status === 'error' && !signin
  const ready = !!server?.live.ready
  const settled = ready || failed

  useEffect(() => {
    void poll()
    if (settled) return
    const t = setInterval(() => void poll(), POLL_MS)
    return () => clearInterval(t)
  }, [poll, settled])

  // An OAuth connector has nothing to show until the browser step is done, so start it right away.
  useEffect(() => {
    if (oauth && !started.current) { started.current = true; void signIn() }
  }, [oauth, signIn])

  useEffect(() => {
    if (failed) void api.mcp.logs(serverId).then((l) => setStderr(l.stderr.slice(-LOG_TAIL))).catch(() => undefined)
  }, [failed, serverId])

  const word = ready ? 'Ready' : failed ? 'Failed' : signin || signing ? 'Needs sign-in' : 'Connecting…'
  return (
    <div className={`mcp-server open connector-status ${ready ? 'ok' : failed ? 'bad' : ''}`} role="status" aria-live="polite">
      <div className="mcp-head">
        {ready ? <Check size={14} /> : failed ? null : <Loader2 size={14} className="spin" />}
        <b>{name}: {word}</b>
        <button className="icon-btn" aria-label="Dismiss" onClick={onClose}><X size={14} /></button>
      </div>
      <div className="mcp-body">
        {ready && <p className="muted small">Connected with {server?.tools.length ?? 0} tool{server?.tools.length === 1 ? '' : 's'}. Find it under Installed.</p>}
        {(signin || signing) && !ready && (
          <div className="mcp-actions">
            <button className="primary-btn small" disabled={signing} onClick={() => void signIn()}>
              <ExternalLink size={12} /> {signing ? 'Waiting for the browser…' : 'Sign in'}
            </button>
          </div>
        )}
        {signErr && <p className="test-msg fail">{signErr}</p>}
        {failed && <p className="test-msg fail">{server?.live.detail || 'It did not start.'}</p>}
        {failed && stderr.length > 0 && <pre className="mcp-log">{stderr.join('\n')}</pre>}
      </div>
    </div>
  )
}
