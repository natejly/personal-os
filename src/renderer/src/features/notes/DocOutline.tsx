import { useMemo } from 'react'
import { activeHeading, outline } from './outline'
import '../../styles/notes.css'

/** The doc's headings as a nested list. Clicking one jumps the editor; the section the caret is in is marked. */
export default function DocOutline({ source, onJump, activeLine }: {
  source: string
  onJump: (line: number) => void
  activeLine?: number
}): JSX.Element {
  const items = useMemo(() => outline(source), [source])
  const active = activeLine === undefined ? -1 : activeHeading(items, activeLine)
  if (items.length === 0) return <div className="outline-empty">Headings you write show up here.</div>
  return (
    <nav className="outline" aria-label="Outline">
      {items.map((it, i) => (
        <button key={it.line} className={`outline-item ${i === active ? 'on' : ''}`} style={{ paddingLeft: 8 + it.depth * 12 }}
          aria-current={i === active ? 'true' : undefined} title={it.text} onClick={() => onJump(it.line)}>
          {it.text}
        </button>
      ))}
    </nav>
  )
}
