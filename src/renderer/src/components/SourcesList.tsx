import type { ChunkRef } from './ChunkViewer'
import { citeInfo, citeTitle, splitSources } from '../lib/remarkCites'

/**
 * A reply's sources, as a compact footer: only the excerpts its text actually cites, numbered 1, 2, 3 in
 * order of first citation (the inline chips carry the same numbers). An excerpt retrieved but never cited
 * is not listed. Each opens the passage, as a chip does. Nothing renders without citations.
 */
export default function SourcesList({ content, chunks, onOpen }: { content: string; chunks: readonly ChunkRef[]; onOpen: (c: ChunkRef) => void }): JSX.Element | null {
  const { cited } = splitSources(content, chunks)
  if (!cited.length) return null
  return (
    <div className="sources">
      <span className="sources-title">Sources</span>
      <ol>
        {cited.map((c, i) => {
          const info = citeInfo(c)
          return (
            <li key={c.n} className="cited">
              <button type="button" title={citeTitle(info)} onClick={() => onOpen(c)}>
                <span className="sources-n">[{i + 1}]</span> {info.label}
              </button>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
