import { useLayoutEffect, useRef, useState } from 'react'
import { placePopup, type CaretRect } from './caretPosition'
import '../../styles/notes.css'

export interface CaretMenuItem { key: string; label: string; hint?: string }

/**
 * A listbox that hangs off the caret. The slash menu and the wikilink picker share it. It lives inside
 * the editor surface, so `anchor` and `bounds` are in the surface's coordinates, and it flips above the
 * caret or slides left when the surface has no room below or to the right.
 */
export default function CaretMenu({ items, active, anchor, bounds, label, onPick, onHover }: {
  items: CaretMenuItem[]
  active: number
  anchor: CaretRect
  bounds: { w: number; h: number }
  label: string
  onPick: (index: number) => void
  onHover: (index: number) => void
}): JSX.Element {
  const box = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ top: anchor.top + anchor.height, left: anchor.left })
  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const p = placePopup(anchor, { w: el.offsetWidth, h: el.offsetHeight }, bounds)
    setPos((cur) => (cur.top === p.top && cur.left === p.left ? cur : { top: p.top, left: p.left }))
  }, [anchor, bounds, items.length])

  // Keep the highlighted row visible as the arrows move it.
  useLayoutEffect(() => {
    box.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [active])

  return (
    <div ref={box} className="caret-menu" role="listbox" aria-label={label} style={{ top: pos.top, left: pos.left }}
      // Pointer-down must not steal focus from the textarea, or the caret (and the menu's trigger) is lost.
      onMouseDown={(e) => e.preventDefault()}>
      {items.map((it, i) => (
        <div key={it.key} role="option" aria-selected={i === active} className={`caret-menu-row ${i === active ? 'on' : ''}`}
          onMouseEnter={() => onHover(i)} onClick={() => onPick(i)}>
          <span className="caret-menu-label">{it.label}</span>
          {it.hint && <span className="caret-menu-hint">{it.hint}</span>}
        </div>
      ))}
    </div>
  )
}
