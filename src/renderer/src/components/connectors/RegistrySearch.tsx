import { useState } from 'react'
import { ExternalLink, Search } from 'lucide-react'
import { api } from '../../lib/api'
import type { McpRegistryResult } from '@shared/types'
import { REGISTRY_LIMIT } from './catalog'

/** Search the public MCP registry. Hits are unverified: "Use this" only fills the add form, which still checks first. */
export default function RegistrySearch({ onUse }: { onUse: (r: McpRegistryResult) => void }): JSX.Element {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<McpRegistryResult[] | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const search = async (): Promise<void> => {
    if (!q.trim()) return
    setBusy(true)
    setError('')
    try {
      const r = await api.mcp.registrySearch(q.trim(), REGISTRY_LIMIT)
      setResults(r.results)
      setError(r.error ?? '')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="registry-search">
      <h3>Search the MCP Registry</h3>
      <p className="muted small">
        Anyone can publish to the registry and Grain has not checked these. A result only fills in the custom form
        so you can run the safety check before adding it.
      </p>
      <form className="add-row" onSubmit={(e) => { e.preventDefault(); void search() }}>
        <input className="search" aria-label="Search the MCP Registry" placeholder="e.g. postgres, notion, weather" value={q}
          onChange={(e) => setQ(e.target.value)} spellCheck={false} />
        <button type="submit" className="ghost-btn" disabled={busy || !q.trim()}><Search size={13} /> {busy ? 'Searching…' : 'Search'}</button>
      </form>
      {error && <p className="test-msg fail">{error}</p>}
      {results && !results.length && !error && <p className="muted small">Nothing found for &ldquo;{q}&rdquo;.</p>}
      {results?.map((r) => (
        <div key={`${r.id}-${r.version}`} className="connector-card">
          <div className="connector-head">
            <span className="connector-title">
              <b>{r.name}</b>
              <small className="muted">{r.version && `v${r.version} · `}{r.transport === 'stdio' ? 'runs here' : 'remote'}</small>
            </span>
            <span className="tag bad" title="Not checked by Grain">Unverified</span>
            <button className="ghost-btn small" aria-label={`Use ${r.name}`} onClick={() => onUse(r)}>Use this</button>
          </div>
          {r.description && <p className="connector-desc">{r.description}</p>}
          <div className="connector-tags">
            {r.secret_keys.length > 0 && <span className="tag">Needs: {r.secret_keys.join(', ')}</span>}
            {r.repository && <a className="tag" href={r.repository} target="_blank" rel="noreferrer"><ExternalLink size={11} /> Source</a>}
          </div>
        </div>
      ))}
    </section>
  )
}
