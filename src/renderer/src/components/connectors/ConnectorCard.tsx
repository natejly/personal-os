import { AlertTriangle, BadgeCheck, Check, ExternalLink } from 'lucide-react'
import type { McpCatalogEntry } from '@shared/types'
import { detectionLabel, iconFor } from './catalog'

// Only what differs from the default (runs on this Mac, no sign-in) earns a chip; the category is the filter above the grid.
const TRANSPORT_WORD = { stdio: '', http: 'Remote', sse: 'Remote (SSE)' } as const
const AUTH_WORD = { none: '', api_key: 'API key', oauth: 'Browser sign-in', env: 'Needs settings' } as const

/** One catalog entry: who makes it, how it runs, what it needs, and whether its launcher is missing. */
export default function ConnectorCard({ entry, warning, onInstall }: {
  entry: McpCatalogEntry; warning: string; onInstall: () => void
}): JSX.Element {
  const Icon = iconFor(entry.icon)
  const installed = entry.installed.length > 0
  const detect = detectionLabel(entry)
  const missing = !!detect && !detect.found
  return (
    <div className="connector-card">
      <div className="connector-head">
        <span className="connector-icon"><Icon size={18} /></span>
        <span className="connector-title">
          <b>{entry.name}</b>
          <small className="muted">
            {entry.publisher}
            {entry.official && <span className="tag verified" title="Maintained by the vendor of the service"><BadgeCheck size={11} /> Official</span>}
          </small>
        </span>
        {installed
          ? <span className="tag verified"><Check size={11} /> Installed</span>
          : missing
            ? <span className="tag" title="Not found on this Mac">Not installed</span>
            : <button className="primary-btn small" aria-label={`Install ${entry.name}`} onClick={onInstall}>Install</button>}
      </div>
      <p className="connector-desc">{entry.description}</p>
      {detect && !installed && <p className="connector-detect">{detect.text}</p>}
      <div className="connector-tags">
        {TRANSPORT_WORD[entry.transport] && <span className="tag">{TRANSPORT_WORD[entry.transport]}</span>}
        {AUTH_WORD[entry.auth] && <span className="tag">{AUTH_WORD[entry.auth]}</span>}
        {entry.docs && <a className="tag" href={entry.docs} target="_blank" rel="noreferrer" aria-label={`${entry.name} documentation`}><ExternalLink size={11} /> Docs</a>}
      </div>
      {warning && !installed && <p className="test-msg fail connector-warn"><AlertTriangle size={12} /> {warning}</p>}
    </div>
  )
}
