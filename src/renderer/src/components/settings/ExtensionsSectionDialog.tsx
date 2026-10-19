import { useEffect, useId, useRef } from 'react'
import type { ReactNode } from 'react'
import './extensionsSection.css'

/** A small confirm dialog: focus moves in, Escape cancels. */
export default function ExtensionsSectionDialog({ title, children, confirm, cancel, danger, busy, onConfirm, onCancel }: {
  title: string; children?: ReactNode; confirm: string; cancel: string; danger?: boolean; busy?: boolean
  onConfirm: () => void; onCancel: () => void
}): JSX.Element {
  const id = useId()
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    ref.current?.focus()
    return () => { before?.focus?.() }
  }, [])
  return (
    <div className="modal-backdrop ext-sheet-backdrop" onClick={onCancel}>
      <div
        ref={ref} tabIndex={-1} role="alertdialog" aria-modal="true" aria-labelledby={id}
        className="modal modal-free ext-dialog"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); onCancel() } }}
      >
        <header><h2 id={id}>{title}</h2></header>
        <div className="ext-sheet-body">{children}</div>
        <footer>
          <button type="button" className="ghost-btn" onClick={onCancel} disabled={busy}>{cancel}</button>
          <button type="button" className={danger ? 'primary-btn ext-danger-btn' : 'primary-btn'} onClick={onConfirm} disabled={busy}>{confirm}</button>
        </footer>
      </div>
    </div>
  )
}
