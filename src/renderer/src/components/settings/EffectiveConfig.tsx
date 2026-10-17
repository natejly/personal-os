import { useEffect, useMemo, useState } from 'react'
import type { EffectiveConfig as Config } from '@shared/types'
import { api } from '../../lib/api'
import { effectiveRows, filterRows, sourceKinds, tabFor } from '../../lib/effectiveConfig'
import { useStore } from '../../store'

/** The settings in force and where each came from, read-only. Follows the open chat and its project; edit a value in the panel its row links to. */
export default function EffectiveConfig(): JSX.Element {
  const [cfg, setCfg] = useState<Config | null>(null)
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState('all')
  const toast = useStore((s) => s.toast)
  const openSettings = useStore((s) => s.openSettings)
  const chatId = useStore((s) => s.focusedConversationId)
  const projectId = useStore((s) => s.conversations.find((c) => c.id === s.focusedConversationId)?.project_id ?? s.projectViewId)
  useEffect(() => { api.config.effective(projectId, chatId).then(setCfg).catch((e: Error) => toast(e.message, 'error')) }, [projectId, chatId, toast])
  const rows = useMemo(() => (cfg ? effectiveRows(cfg) : []), [cfg])
  const shown = useMemo(() => filterRows(rows, query, kind), [rows, query, kind])
  const copy = (): void => { if (cfg) void navigator.clipboard.writeText(JSON.stringify(cfg, null, 2)).then(() => toast('Copied the effective configuration', 'info')) }
  if (!cfg) return <p className="muted small">Loading…</p>
  return (
    <div className="eff-config">
      <p className="muted small">What Grain runs on for {chatId ? 'this chat' : projectId ? 'this project' : 'everything'}, with the source of each value. Secrets show only whether they are set. Nothing is edited here.</p>
      <div className="eff-bar">
        <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search settings" aria-label="Search the effective configuration" spellCheck={false} />
        <div className="eff-chips" role="group" aria-label="Source">
          {['all', 'changed', ...sourceKinds(rows)].map((k) => (
            <button key={k} type="button" className={`ghost-btn ${kind === k ? 'on' : ''}`} aria-pressed={kind === k} onClick={() => setKind(k)}>{k === 'changed' ? 'Differs from default' : k}</button>
          ))}
        </div>
        <button className="ghost-btn" onClick={copy}>Copy as JSON</button>
      </div>
      <table className="eff-table">
        <thead><tr><th>Setting</th><th>Value</th><th>Source</th></tr></thead>
        <tbody>
          {shown.map((r) => {
            const tab = tabFor(r.key)
            return (
              <tr key={r.key} data-key={r.key}>
                <td>{tab ? <button type="button" className="link-btn" onClick={() => openSettings(tab)}>{r.key}</button> : r.key}</td>
                <td><code>{r.value.length > 120 ? `${r.value.slice(0, 120)}…` : r.value}</code></td>
                <td title={r.shadowed.length ? `Shadows ${r.shadowed.join('; ')}` : undefined}>{r.source}{r.scope !== 'global' ? ` (${r.scope})` : ''}{r.legacy ? ' (before sources were recorded)' : ''}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {!shown.length && <p className="muted small">No setting matches.</p>}
    </div>
  )
}
