import { useEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from 'react'

interface PopMenuProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children' | 'onClick'> {
  /** What the button shows. */
  label: ReactNode
  /** What the panel above it holds; `close` shuts it. */
  children: (close: () => void) => ReactNode
}

/** A button that opens a small panel above it. An outside click or Escape closes the panel. */
export default function PopMenu({ label, children, ...button }: PopMenuProps): JSX.Element {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent): void => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent): void => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])
  return (
    <span className="pop-menu" ref={root}>
      <button type="button" {...button} aria-haspopup="true" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{label}</button>
      {open && <div className="pop-menu-panel">{children(() => setOpen(false))}</div>}
    </span>
  )
}
