import { useEffect } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { useStore } from '../../store'
import type { SidePanelTabProps } from '../host'
import { type Open, canZoom, isEmpty, scopeFor, spanLabel, tokenLabel, tokenUse, visibleRows } from './cover'
import { useDigest } from './store'

const NONE: Open = {}

/** The side panel's History digest card: the active scope's current cover, with a block opened into its two halves on click. */
export default function DigestTab({ conversationId }: SidePanelTabProps): JSX.Element {
  const projectId = useStore((s) => s.conversations.find((c) => c.id === conversationId)?.project_id ?? s.draftProjectId)
  const scope = scopeFor(projectId)
  const { cover, open, zoomed, loading, error, loadCover, toggle } = useDigest(useShallow((s) => ({
    cover: s.covers[scope], open: s.open[scope] ?? NONE, zoomed: s.zoomed, loading: !!s.loading[scope], error: s.error, loadCover: s.loadCover, toggle: s.toggle
  })))
  useEffect(() => { void loadCover(scope) }, [scope, loadCover])

  if (!cover) return <p className="set-help" role="status">{error ?? (loading ? 'Loading…' : 'Nothing in the digest yet')}</p>
  if (isEmpty(cover)) return <p className="set-help">Nothing in the digest yet</p>
  const rows = visibleRows(cover.nodes, open, zoomed, scope)
  return (
    <div className="memtree" data-testid="memtree-cover">
      <p className="set-help">
        {tokenLabel(cover.used_tokens, cover.budget_tokens)}
        <progress value={tokenUse(cover.used_tokens, cover.budget_tokens)} max={100} style={{ marginLeft: 8, width: 60 }} />
      </p>
      {error && <p className="set-help" role="status">{error}</p>}
      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {rows.map(({ node, depth }) => {
          const zoomable = canZoom(node)
          return (
            <li key={`${depth}:${node.range}`} style={{ paddingLeft: depth * 12, margin: '6px 0' }}>
              <div
                role={zoomable ? 'button' : undefined}
                tabIndex={zoomable ? 0 : undefined}
                aria-expanded={zoomable ? !!open[node.range] : undefined}
                style={{ cursor: zoomable ? 'pointer' : 'default' }}
                onClick={zoomable ? () => toggle(scope, node.range) : undefined}
                onKeyDown={zoomable ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(scope, node.range) } } : undefined}
              >
                <small className="set-help">{zoomable ? (open[node.range] ? '▾ ' : '▸ ') : ''}{spanLabel(node)}</small>
                {node.tainted && <small style={{ marginLeft: 6, opacity: 0.8 }}>unverified</small>}
                <div style={node.pending ? { opacity: 0.6, fontStyle: 'italic' } : undefined}>{node.pending ? 'not summarised yet' : node.text}</div>
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
