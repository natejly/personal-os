import type { KeyboardEvent } from 'react'

/** What a clickable row needs to be reachable and usable from the keyboard. */
export interface RowButton {
  role: 'button'
  tabIndex: 0
  onClick: () => void
  onKeyDown: (e: KeyboardEvent<HTMLElement>) => void
}

/**
 * Props for a row that acts as a button but cannot be a `<button>` (it holds its own buttons, or is a
 * drag source). Enter and Space activate it, the way they would a real button. A key pressed on a
 * control inside the row belongs to that control, so only the row's own keydown counts.
 */
export function rowButton(activate: () => void): RowButton {
  return {
    role: 'button',
    tabIndex: 0,
    onClick: activate,
    onKeyDown: (e) => {
      if (e.target !== e.currentTarget || (e.key !== 'Enter' && e.key !== ' ')) return
      e.preventDefault()
      activate()
    }
  }
}
