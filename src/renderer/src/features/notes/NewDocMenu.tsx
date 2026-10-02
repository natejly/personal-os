import { useEffect, useRef, useState } from 'react'
import { CalendarDays, ChevronDown, Plus } from 'lucide-react'
import { TEMPLATES } from './templates'
import '../../styles/notes.css'

/** A split "New" button: the main half makes a blank doc, the chevron opens templates and today's daily note. */
export default function NewDocMenu({ onCreate, onDaily }: {
  onCreate: (t: { title: string; content: string }) => void
  onDaily: () => void
}): JSX.Element {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent): void => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent): void => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [open])

  const make = (id: string): void => {
    const t = TEMPLATES.find((x) => x.id === id)
    if (!t) return
    const now = new Date()
    setOpen(false)
    onCreate({ title: t.title(now), content: t.body(now) })
  }

  return (
    <div className="newdoc" ref={root}>
      <button className="newdoc-main ghost-btn" title="New doc" onClick={() => make('blank')}><Plus size={13} /> New</button>
      <button className="newdoc-chev ghost-btn" aria-haspopup="menu" aria-expanded={open} aria-label="New from template" onClick={() => setOpen((o) => !o)}>
        <ChevronDown size={12} />
      </button>
      {open && (
        <div className="notes-menu" role="menu">
          <button role="menuitem" className="notes-menu-row" onClick={() => { setOpen(false); onDaily() }}>
            <CalendarDays size={13} /> Today's note
          </button>
          <div className="notes-menu-sep" />
          <div className="notes-menu-label">From template</div>
          {TEMPLATES.filter((t) => t.id !== 'blank').map((t) => (
            <button key={t.id} role="menuitem" className="notes-menu-row" onClick={() => make(t.id)}>{t.name}</button>
          ))}
        </div>
      )}
    </div>
  )
}
