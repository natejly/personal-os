import { useState, type ReactNode } from 'react'

const KEY = 'grain.settings.disclosure.'

function read(id: string): boolean {
  try { return localStorage.getItem(KEY + id) === '1' } catch { return false }
}

/** A collapsed-by-default group of rarely changed rows. Open state is remembered per id. Children are not mounted while closed. */
export default function Disclosure({ id, title, children }: { id: string; title: ReactNode; children: ReactNode }): JSX.Element {
  const [open, setOpen] = useState(() => read(id))
  return (
    <details className="settings-disclosure" data-disclosure={id} open={open}
      onToggle={(e) => {
        const next = (e.currentTarget as HTMLDetailsElement).open
        if (next === open) return
        setOpen(next)
        try { localStorage.setItem(KEY + id, next ? '1' : '0') } catch { /* ignore */ }
      }}>
      <summary>{title}</summary>
      {open && children}
    </details>
  )
}
