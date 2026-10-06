import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../lib/api'
import { useStore } from '../../store'
import type { McpCatalog, McpCatalogEntry, McpRegistryResult } from '@shared/types'
import ConnectorCard from './ConnectorCard'
import { InstallForm, InstallStatus } from './InstallForm'
import RegistrySearch from './RegistrySearch'
import { filterCatalog, runtimeWarning } from './catalog'

const ALL = 'All'

/** The catalog of ready-made connectors, install forms, and the open registry search beneath it. */
export default function CatalogBrowser({ onUseRegistry, onInstalled }: {
  onUseRegistry: (r: McpRegistryResult) => void
  /** Called once a connector is saved, so the Installed list can reload. */
  onInstalled: () => void
}): JSX.Element {
  const toast = useStore((s) => s.toast)
  const [catalog, setCatalog] = useState<McpCatalog | null>(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState(ALL)
  const [installing, setInstalling] = useState<McpCatalogEntry | null>(null)
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState('')
  const [status, setStatus] = useState<{ id: string; name: string; oauth: boolean } | null>(null)

  const load = useCallback(async () => {
    try { setCatalog(await api.mcp.catalog()); setError('') } catch (e) { setError((e as Error).message) }
  }, [])
  useEffect(() => { void load() }, [load])

  const shown = useMemo(() => (catalog ? filterCatalog(catalog.entries, query, category) : []), [catalog, query, category])

  const install = async (values: Record<string, string>): Promise<void> => {
    if (!installing) return
    setBusy(true)
    setFormError('')
    try {
      const s = await api.mcp.install(installing.id, values)
      setStatus({ id: s.id, name: s.name, oauth: installing.auth === 'oauth' })
      setInstalling(null)
      onInstalled()
      void load()
      toast(`Installed ${s.name}. Its tools ask before they run.`)
    } catch (e) {
      setFormError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="catalog-browser">
      <div className="add-row">
        <input className="search" aria-label="Search connectors" placeholder="Search connectors" value={query}
          onChange={(e) => setQuery(e.target.value)} spellCheck={false} />
      </div>
      {catalog && (
        <div className="seg catalog-cats" role="group" aria-label="Category">
          {[ALL, ...catalog.categories].map((c) => (
            <button key={c} type="button" className={category === c ? 'on' : ''} aria-pressed={category === c} onClick={() => setCategory(c)}>{c}</button>
          ))}
        </div>
      )}

      {status && <InstallStatus key={status.id} serverId={status.id} name={status.name} oauth={status.oauth} onClose={() => setStatus(null)} />}
      {installing && (
        <InstallForm entry={installing} busy={busy} error={formError} onSubmit={(v) => void install(v)}
          onCancel={() => { setInstalling(null); setFormError('') }} />
      )}

      {error && <p className="test-msg fail">{error}</p>}
      {catalog && !shown.length && <p className="empty-row">No connectors match.</p>}
      <div className="connector-grid">
        {shown.map((e) => (
          <ConnectorCard key={e.id} entry={e} warning={runtimeWarning(e, catalog?.runtimes ?? {})}
            onInstall={() => { setInstalling(e); setFormError(''); setStatus(null) }} />
        ))}
      </div>

      <RegistrySearch onUse={onUseRegistry} />
    </div>
  )
}
