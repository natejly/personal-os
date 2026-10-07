import { useCallback, useEffect, useState } from 'react'
import { Lock } from 'lucide-react'
import { api } from '../../lib/api'
import type { McpImportSource, McpServer } from '@shared/types'
import { joinArgv } from './catalog'

/** Servers already set up in another app, offered for import. Only key names are shown; values never leave the backend. */
export default function ImportDialog({ onImported }: { onImported: () => void }): JSX.Element {
  const [sources, setSources] = useState<McpImportSource[] | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ created: McpServer[]; skipped: { ref: string; reason: string }[] } | null>(null)
  const [text, setText] = useState('')
  const [pasteBusy, setPasteBusy] = useState(false)
  const [pasteError, setPasteError] = useState('')
  const [pasted, setPasted] = useState<{ created: McpServer[]; skipped: { name: string; reason: string }[] } | null>(null)

  const load = useCallback(async () => {
    try { setSources((await api.mcp.importSources()).sources); setError('') } catch (e) { setError((e as Error).message) }
  }, [])
  useEffect(() => { void load() }, [load])

  const toggle = (ref: string): void => setPicked((p) => { const n = new Set(p); if (!n.delete(ref)) n.add(ref); return n })

  const run = async (): Promise<void> => {
    setBusy(true)
    try {
      setResult(await api.mcp.importServers([...picked]))
      setPicked(new Set())
      onImported()
      void load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // The pasted text is never echoed back: a failure shows the backend's reason only, and the field keeps what was typed.
  const importPasted = async (): Promise<void> => {
    setPasteBusy(true)
    setPasteError('')
    setPasted(null)
    try {
      const r = await api.mcp.importJson(text)
      setPasted(r)
      if (r.created.length) { setText(''); onImported(); void load() }
    } catch (e) {
      setPasteError((e as Error).message)
    } finally {
      setPasteBusy(false)
    }
  }

  const nameOf = (ref: string): string => sources?.flatMap((s) => s.servers).find((x) => x.ref === ref)?.name ?? ref

  return (
    <div className="import-panel">
      <p className="muted small">
        Bring over connectors you already set up in another app. Secret-looking values go to the secret store, and
        nothing is turned on until you enable it here.
      </p>
      {error && <p className="test-msg fail">{error}</p>}
      {sources?.map((src) => (
        <section key={src.id} className="mcp-server open import-source">
          <div className="mcp-head">
            <b>{src.label}</b>
            <span className={`tag ${src.error ? 'bad' : src.found ? 'verified' : ''}`}>{src.error ? 'error' : src.found ? 'found' : 'not found'}</span>
          </div>
          <div className="mcp-body">
            <small className="muted mono">{src.path}</small>
            {src.error && <p className="test-msg fail">{src.error}</p>}
            {src.found && !src.error && !src.servers.length && <p className="muted small">No servers in this file.</p>}
            {src.servers.map((s) => (
              <label key={s.ref} className="import-row chip-check-row">
                <input type="checkbox" disabled={s.installed || !!s.duplicate_of} checked={picked.has(s.ref)} onChange={() => toggle(s.ref)} aria-label={`Import ${s.name}`} />
                <span className="toggle-text">
                  <b>{s.name} {s.installed && <span className="tag">already added</span>}
                    {s.duplicate_of && <span className="tag">same as {s.duplicate_of}</span>}
                    {s.enabled === false && <span className="tag">disabled there</span>}</b>
                  <small className="muted mono">{s.transport === 'stdio' ? joinArgv(s.command, s.args) : s.url}</small>
                  {(s.env_keys.length > 0 || s.header_keys.length > 0) && (
                    <small className="muted">
                      {[...s.env_keys, ...s.header_keys].map((k) => (
                        <span key={k} className="import-key">
                          {(s.secret_keys.includes(k) || s.header_keys.includes(k)) && <Lock size={10} aria-label="goes to the secret store" />}{k}
                        </span>
                      ))}
                    </small>
                  )}
                </span>
              </label>
            ))}
          </div>
        </section>
      ))}
      <div className="mcp-actions">
        <button className="primary-btn" disabled={busy || !picked.size} onClick={() => void run()}>
          {busy ? 'Importing…' : `Import selected${picked.size ? ` (${picked.size})` : ''}`}
        </button>
      </div>
      <section className="mcp-server open import-source import-paste">
        <div className="mcp-head"><b>Paste JSON config</b></div>
        <div className="mcp-body">
          <small className="muted">
            Accepts <code>{'{"mcpServers": {...}}'}</code>, a bare name-to-server map, or one server object. Values go to
            the secret store; nothing is turned on.
          </small>
          <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} aria-label="Config JSON"
            placeholder={'{"mcpServers": {"name": {"command": "npx", "args": ["-y", "some-server"]}}}'} />
          {pasteError && <p className="test-msg fail">{pasteError}</p>}
          <div className="mcp-actions">
            <button className="primary-btn" disabled={pasteBusy || !text.trim()} onClick={() => void importPasted()}>
              {pasteBusy ? 'Importing…' : 'Import'}
            </button>
          </div>
          {pasted && (
            <div className="mcp-report pass" role="status">
              {pasted.created.length > 0 && <p><b>Imported:</b> {pasted.created.map((s) => s.name).join(', ')}. Find them under Installed.</p>}
              {pasted.skipped.length > 0 && (
                <ul className="mcp-limits-list">{pasted.skipped.map((k) => <li key={k.name}><b>Skipped {k.name}:</b> {k.reason}</li>)}</ul>
              )}
              {!pasted.created.length && !pasted.skipped.length && <p>Nothing to import.</p>}
            </div>
          )}
        </div>
      </section>
      {result && (
        <div className="mcp-report pass" role="status">
          {result.created.length > 0 && <p><b>Imported:</b> {result.created.map((s) => s.name).join(', ')}. Find them under Installed.</p>}
          {result.skipped.length > 0 && (
            <ul className="mcp-limits-list">{result.skipped.map((k) => <li key={k.ref}><b>Skipped {nameOf(k.ref)}:</b> {k.reason}</li>)}</ul>
          )}
        </div>
      )}
    </div>
  )
}
