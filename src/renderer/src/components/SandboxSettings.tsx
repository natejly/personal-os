import { useEffect, useState } from 'react'
import type { SandboxStatus, Settings } from '@shared/types'
import { api } from '../lib/api'
import { ageLabel, sandboxKey, sandboxTitle } from '../lib/runningViews'

/**
 * The Linux sandbox section of Settings > Tools: whether the container runtime answers (and why not), the settings
 * every new container is made with, and the containers that exist now with a Reset each. The settings go through the
 * modal's draft like the rest; the list and Reset act at once, because they are things on the machine.
 */
export default function SandboxSettings({ draft, patch }: { draft: Settings; patch: (p: Partial<Settings>) => void }): JSX.Element {
  const [st, setSt] = useState<SandboxStatus | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = (): void => { void api.sandboxes().then(setSt).catch((e) => setError((e as Error).message)) }
  useEffect(load, [])
  const reset = async (key: string): Promise<void> => {
    setBusy(key)
    setError(null)
    try { await api.resetSandbox(key) } catch (e) { setError((e as Error).message) } finally { setBusy(null); load() }
  }
  return (
    <div className="workspace-roots">
      <span><b>Linux sandbox</b></span>
      <p className="muted small">
        {st === null ? 'Checking the container runtime…'
          : st.available ? `Available through ${st.runtime}. The sandbox tools run commands in a container inside a Linux VM.`
          : `Unavailable, so the sandbox tools are hidden: ${st.reason}. Install a container runtime (for example colima with the docker CLI) and start it.`}
        {' '}<button className="link small" onClick={load}>Check again</button>
      </p>
      <label className="toggle-row plain">
        <span className="toggle-text"><b>Share the desk folder with its sandbox</b><small>A desk's Linux sandbox sees that desk's workspace at /workspace/desk. Nothing else of your Mac is shared.</small></span>
        <input type="checkbox" checked={draft.sandboxMountDesk !== false} onChange={(e) => patch({ sandboxMountDesk: e.target.checked })} /><span className="switch" />
      </label>
      <label className="toggle-row plain">
        <span className="toggle-text"><b>Network in new sandboxes</b><small>Off by default. When on, anything a sandbox command prints may be third-party text, so it marks the chat as having read untrusted content and later external actions ask. Applies to containers created or restored after saving.</small></span>
        <input type="checkbox" checked={!!draft.sandboxNetwork} onChange={(e) => patch({ sandboxNetwork: e.target.checked })} /><span className="switch" />
      </label>
      <label><span>Image <small className="muted">(what a fresh sandbox starts from)</small></span>
        <input value={draft.sandboxImage ?? ''} placeholder="python:3.12-slim" spellCheck={false} onChange={(e) => patch({ sandboxImage: e.target.value })} />
      </label>
      <label><span>Runtime</span>
        <select value={draft.sandboxRuntime ?? 'docker'} onChange={(e) => patch({ sandboxRuntime: e.target.value as Settings['sandboxRuntime'] })}>
          <option value="docker">docker</option><option value="podman">podman</option><option value="nerdctl">nerdctl</option>
        </select>
      </label>
      {st?.available && (st.items.length ? (
        <ul className="plain-list">
          {st.items.map((s) => (
            <li key={s.name}>
              <span><b>{sandboxTitle(s)}</b> <small className="muted">
                {s.status}{s.last_used ? ` · used ${ageLabel(s.last_used)} ago` : ''}
                {s.checkpoints.length ? ` · checkpoints: ${s.checkpoints.join(', ')}` : ''}
                {s.networked ? ' · networked' : ''}{s.holds_import ? ' · holds library text' : ''}
              </small></span>
              <button className="link small" disabled={busy !== null} title="Remove the container and its checkpoints; the next sandbox call starts clean"
                onClick={() => void reset(sandboxKey(s))}>{busy === sandboxKey(s) ? 'resetting…' : 'Reset'}</button>
            </li>
          ))}
        </ul>
      ) : <p className="muted small">No sandboxes exist right now.</p>)}
      {error && <p className="muted small">{error}</p>}
    </div>
  )
}
