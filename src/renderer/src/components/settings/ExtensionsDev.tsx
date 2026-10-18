import { useCallback, useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { contributionSummary, formatEvery, formatMs, mutedHooks, unsupportedWarning } from '../../lib/extensionsDev'
import type { ExtensionInfo, ExtensionsDev as Listing } from '@shared/types'

/** Settings > Advanced > Developer: every loaded extension with what it registered, how its handlers behave, and enable, disable and reload. */
export default function ExtensionsDev({ onChanged }: { onChanged: () => Promise<void> }): JSX.Element {
  const [listing, setListing] = useState<Listing | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback((): void => {
    api.devExtensions.list().then(setListing).catch((e: Error) => setError(e.message))
  }, [])
  useEffect(load, [load])
  const act = (run: () => Promise<ExtensionInfo>): void => {
    setError(null)
    run().then(onChanged).then(load).catch((e: Error) => { setError(e.message); load() }) // a failed reload has already unloaded the extension
  }
  return (
    <>
      <h4>Extensions</h4>
      <div className="test-row">
        <button type="button" className="ghost-btn" onClick={load}>Refresh</button>
        {!!listing?.failed.length && <button type="button" className="ghost-btn" onClick={() => api.devExtensions.clearFailed().then(load).catch((e: Error) => setError(e.message))}>Clear failed loads</button>}
        {error && <span className="test-msg" role="status">{error}</span>}
      </div>
      {listing && !listing.live && <p className="set-help">Reloading needs the backend to be started with GRAIN_EXT_DEV=1.</p>}
      {listing?.extensions.map((x) => (
        <details key={x.id} className="adv-group">
          <summary>{x.name} <small>{x.id} {x.version} {x.tier}{x.enabled ? '' : ' (off)'}{mutedHooks(x).length ? ' (muted handlers)' : ''}</small></summary>
          <div className="test-row">
            {x.toggleable && <button type="button" className="ghost-btn" onClick={() => act(() => (x.enabled ? api.devExtensions.disable(x.id) : api.devExtensions.enable(x.id)))}>{x.enabled ? 'Disable' : 'Enable'}</button>}
            {x.reloadable && <button type="button" className="ghost-btn" disabled={!listing.live} onClick={() => act(() => api.devExtensions.reload(x.id))}>Reload</button>}
          </div>
          <p className="set-help">{[...contributionSummary(x), ...x.permissions].join(' · ') || 'Registers nothing.'}</p>
          {unsupportedWarning(x.permissions) && <p className="set-help" role="status">{unsupportedWarning(x.permissions)}</p>}
          {x.jobs.length > 0 && (
            <table>
              <thead><tr><th>Job</th><th>Every</th><th>Runs</th><th>State</th></tr></thead>
              <tbody>
                {x.jobs.map((j) => (
                  <tr key={j.name} title={j.lastError ?? ''}>
                    <td>{j.name}</td><td>{formatEvery(j.everySeconds)}</td><td>{j.runs}</td><td>{j.lastError ? 'failing' : 'ok'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {x.hooks.length > 0 && (
            <table>
              <thead><tr><th>Hook</th><th>Calls</th><th>p50</th><th>p95</th><th>State</th><th>Recent payloads</th></tr></thead>
              <tbody>
                {x.hooks.map((h) => (
                  <tr key={h.hook} title={h.errors.join('\n')}>
                    <td>{h.hook}</td><td>{h.calls}</td><td>{formatMs(h.p50Ms)}</td><td>{formatMs(h.p95Ms)}</td>
                    <td>{h.muted ? 'muted' : h.errors.length ? `${h.errors.length} recent error(s)` : 'ok'}</td>
                    <td>{h.payloads.length > 0 && <details><summary>{h.payloads.length}</summary><pre>{h.payloads.join('\n')}</pre></details>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </details>
      ))}
      {listing?.failed.map((f) => <p key={f.path} className="set-help">{f.path}: {f.error}</p>)}
    </>
  )
}
