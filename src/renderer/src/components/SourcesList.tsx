import type { ChunkRef } from './ChunkViewer'
import { citeInfo, citeTitle, splitSources } from '../lib/remarkCites'

/**
 * A reply's numbered excerpts under it, collapsed: the ones its text cites first, in bold, then the ones it
 * consulted without citing, muted. Each opens the passage, as a chip does. Nothing renders without excerpts.
 */
export default function SourcesList({ content, chunks, onOpen }: { content: string; chunks: readonly ChunkRef[]; onOpen: (c: ChunkRef) => void }): JSX.Element | null {
  const { cited, consulted } = splitSources(content, chunks)
  const all = [...cited, ...consulted]
  if (!all.length) return null
  return (
    <details className="sources">
      <summary>Sources ({all.length})</summary>
      <ul>
        {all.map((c) => {
          const info = citeInfo(c)
          return (
            <li key={c.n} className={cited.includes(c) ? 'cited' : 'consulted'}>
              <button type="button" title={citeTitle(info)} onClick={() => onOpen(c)}>
                <span className="sources-n">[{c.n}]</span> {info.label}
              </button>
            </li>
          )
        })}
      </ul>
    </details>
  )
}
