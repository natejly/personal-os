import { useEffect, useId, useRef, type MouseEvent as ReactMouseEvent, type RefObject } from 'react'

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

const focusable = (root: HTMLElement): HTMLElement[] =>
  Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetWidth || el.offsetHeight)

export interface Modal {
  /** Put this on the <h2> title; the dialog is named by it. */
  titleId: string
  /** Spread on the `.modal-backdrop` div. */
  backdrop: { onMouseDown: (e: ReactMouseEvent) => void; onMouseUp: (e: ReactMouseEvent) => void }
  /** Spread on the `.modal` div. */
  modal: { ref: RefObject<HTMLDivElement>; role: 'dialog'; 'aria-modal': true; 'aria-labelledby': string; tabIndex: -1 }
}

/**
 * Dialog behaviour shared by every modal: Escape closes (from anywhere inside, text inputs
 * included — none of our fields use Escape themselves), the backdrop closes only when the
 * press *and* the release land on it, so dragging a selection out of the modal is safe.
 * Tab wraps inside the dialog and focus returns to whatever opened it. Mark a control
 * `autoFocus` for initial focus; otherwise focus lands on the dialog itself.
 */
export function useModal(close: () => void): Modal {
  const ref = useRef<HTMLDivElement>(null)
  const pressedBackdrop = useRef(false)
  const onClose = useRef(close)
  onClose.current = close
  const titleId = useId()

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    if (ref.current && !ref.current.contains(document.activeElement)) ref.current.focus()

    const onKey = (e: KeyboardEvent): void => {
      const el = ref.current
      if (e.defaultPrevented || !el) return
      if (e.key === 'Escape') return onClose.current()
      if (e.key !== 'Tab') return
      const items = focusable(el)
      if (!items.length) return
      const first = items[0]
      const last = items[items.length - 1]
      const at = document.activeElement
      if (!el.contains(at)) { e.preventDefault(); first.focus() }
      else if (e.shiftKey && at === first) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && at === last) { e.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      if (opener?.isConnected) opener.focus()
    }
  }, [])

  return {
    titleId,
    backdrop: {
      onMouseDown: (e) => { pressedBackdrop.current = e.target === e.currentTarget },
      onMouseUp: (e) => {
        const onBackdrop = pressedBackdrop.current && e.target === e.currentTarget
        pressedBackdrop.current = false
        if (onBackdrop) onClose.current()
      }
    },
    modal: { ref, role: 'dialog', 'aria-modal': true, 'aria-labelledby': titleId, tabIndex: -1 }
  }
}
