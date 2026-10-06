import { useEffect, useRef, useState } from 'react'

/**
 * A one-line note typed in place of a prompt dialog (Electron has no `window.prompt`). Enter submits, Escape
 * cancels, focus goes to the field on mount and back to whatever had it when it closes. `optional` lets an
 * empty note through (a rejection with no reason).
 */
export default function InlineNote({ placeholder, submitLabel, optional = false, danger = false, onSubmit, onCancel }: {
  placeholder: string
  submitLabel: string
  optional?: boolean
  danger?: boolean
  onSubmit: (note: string) => void
  onCancel: () => void
}): JSX.Element {
  const [text, setText] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const opener = useRef<Element | null>(null)
  useEffect(() => {
    opener.current = document.activeElement
    input.current?.focus()
    return () => { if (opener.current instanceof HTMLElement && opener.current.isConnected) opener.current.focus() }
  }, [])
  const ready = optional || text.trim().length > 0
  const submit = (): void => { if (ready) onSubmit(text.trim()) }
  return (
    <form className="inline-note" onSubmit={(e) => { e.preventDefault(); submit() }}>
      <input
        ref={input}
        value={text}
        placeholder={placeholder}
        aria-label={placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onCancel() } }}
      />
      <button type="submit" className={danger ? 'ghost-btn danger' : 'primary-btn'} disabled={!ready}>{submitLabel}</button>
      <button type="button" className="ghost-btn" onClick={onCancel}>Cancel</button>
    </form>
  )
}
