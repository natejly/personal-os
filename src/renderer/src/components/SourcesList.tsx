import type { ChunkRef } from './ChunkViewer'
import { citeInfo, citeTitle, splitSources } from '../lib/remarkCites'

/**
 * A reply's numbered excerpts under it, collapsed: only the ones its text actually cites, in first-use
 * order. An excerpt retrieved but never cited is not listed. Each opens the passage, as a chip does.
 * Nothing renders without citations.
 */
export default function SourcesList({ content, chunks, onOpen }: { content: string; chunks: readonly ChunkRef[]; onOpen: (c: ChunkRef) => void }): JSX.Element | null {
  const { cited } = splitSources(content, chunks)
  if (!cited.length) return null
  return (
    <details className="sources">
      <summary>Sources ({cited.length})</summary>
      <ul>
        {cited.map((c) => {
          const info = citeInfo(c)
          return (
            <li key={c.n} className="cited">
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
