import { useCallback, useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { useStore } from '../../store'
import type { ExtensionCard, ExtensionsReview as Listing } from '@shared/types'

/** The part of a unified diff line that says how to colour it. */
const lineClass = (line: string): string =>
  line.startsWith('+') && !line.startsWith('+++') ? 'diff-add' : line.startsWith('-') && !line.startsWith('---') ? 'diff-del' : ''

function Facts({ card }: { card: ExtensionCard }): JSX.Element {
  const tools = card.contributes.tools ?? []
  return (
    <>
      {card.description && <p className="set-help">{card.description}</p>}
      <p className="set-help">
        {[tools.length ? `Tools: ${tools.map((t) => `${t.name} (${t.danger})`).join(', ')}` : '',
          card.contributes.views?.length ? `Views: ${card.contributes.views.map((v) => v.label).join(', ')}` : '',
          card.hooks.length ? `Hooks: ${card.hooks.join(', ')}` : '',
          card.permissions.length ? `Permissions: ${card.permissions.join(', ')}` : 'No permissions.',
          card.settings.length ? `Settings: ${card.settings.join(', ')}` : ''].filter(Boolean).join(' · ')}
      </p>
      {card.tainted && (
        <p className="set-help" role="status">
          The chat that drafted this had read untrusted content{card.taintSources.length ? ` (${card.taintSources.join(', ')})` : ''}.
          {card.refused.length > 0 && ` It cannot hold ${card.refused.join(' or ')}.`}
        </p>
      )}
    </>
  )
}

/** Settings > Advanced > Extensions: extensions the assistant drafted wait here as a diff; accepting installs one switched off, and only Enable here turns it on. */
export default function ExtensionsReview(): JSX.Element {
  const [listing, setListing] = useState<Listing | null>(null)
  const [granted, setGranted] = useState<Record<string, string[]>>({})
  const [error, setError] = useState<string | null>(null)
  const load = useCallback((): void => { api.extensions.list().then(setListing).catch((e: Error) => setError(e.message)) }, [])
  useEffect(load, [load])
  const act = (run: () => Promise<Listing>): void => {
    setError(null)
    run().then((next) => { setListing(next); return api.settings.get() }).then((settings) => useStore.setState({ settings })).catch((e: Error) => setError(e.message))
  }
  const grant = (id: string, perm: string, on: boolean): void =>
    setGranted((g) => ({ ...g, [id]: on ? [...(g[id] ?? []), perm] : (g[id] ?? []).filter((p) => p !== perm) }))
  if (!listing) return <>{error && <p className="set-help" role="status">{error}</p>}</>
  return (
    <>
      <p className="set-help">Extensions the assistant writes for you wait here. Accepting installs one switched off; you turn it on yourself.</p>
      {error && <p className="set-help" role="status">{error}</p>}
      {listing.proposals.map((p) => (
        <details key={p.id} className="adv-group" open>
          <summary>{p.name} <small>{p.id} {p.version}{p.update ? ' (update)' : ''}</small></summary>
          <Facts card={p} />
          {p.findings.map((f) => <p key={f} className="set-help">{f}</p>)}
          {p.changes.map((c) => (
            <details key={c.path}>
              <summary>{c.path} <small>{c.status}</small></summary>
              <div className="desk-diff">
                {c.diff.split('\n').map((line, i) => <div key={i} className={`desk-diff-line ${lineClass(line)}`}>{line}</div>)}
              </div>
            </details>
          ))}
          <div className="test-row">
            <button type="button" className="ghost-btn" onClick={() => act(() => api.extensions.accept(p.id, p.digest))}>Accept</button>
            <button type="button" className="ghost-btn" onClick={() => act(() => api.extensions.reject(p.id))}>Reject</button>
          </div>
        </details>
      ))}
      {listing.installed.map((x) => (
        <details key={x.id} className="adv-group">
          <summary>{x.name} <small>{x.id} {x.version} ({x.enabled ? 'on' : 'off'}{x.changed ? ', files changed' : ''})</small></summary>
          <Facts card={x} />
          {x.changed && <p className="set-help" role="status">Its files are not the ones you accepted, so it stays off until the assistant proposes it again.</p>}
          {!x.enabled && x.needsGrant.map((perm) => (
            <label key={perm}>
              <input type="checkbox" checked={(granted[x.id] ?? []).includes(perm)} onChange={(e) => grant(x.id, perm, e.target.checked)} />
              {' '}Allow {perm}: it can narrow or change what you have approved
            </label>
          ))}
          <div className="test-row">
            {x.enabled
              ? <button type="button" className="ghost-btn" onClick={() => act(() => api.extensions.disable(x.id))}>Disable</button>
              : <button type="button" className="ghost-btn" disabled={x.changed || x.refused.length > 0} onClick={() => act(() => api.extensions.enable(x.id, granted[x.id] ?? []))}>Enable</button>}
          </div>
        </details>
      ))}
      {!listing.proposals.length && !listing.installed.length && <p className="set-help">Nothing waiting and nothing installed.</p>}
    </>
  )
}
