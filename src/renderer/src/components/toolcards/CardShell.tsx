import { useRef, useState, type ReactNode } from 'react'
import { Pencil } from 'lucide-react'
import type { ToolEvent } from '@shared/types'
import { changedKeys, wasEdited } from '../../lib/toolDisplay'
import { RawDetails, ResultBlock, StatusChip } from './parts'
import type { ToolCardProps } from './registry'
import './toolcards.css'

/**
 * The chrome every dedicated card shares: header (icon, title, subject, status), the card's own body, the result,
 * a Details disclosure for the raw call, and Approve / Deny while the call waits, with the standing grants
 * (`rules`) as a quieter line under them.
 *
 * Approve is also ⌘↵ / Ctrl↵ from anywhere inside a pending card. Deny is never a shortcut: a stray key must
 * not be able to refuse (or grant) an outward action on its own, so Deny is a click.
 */
export default function CardShell({ event, pending, decide, rules, icon, title, subject, children, getEdited, invalid, approveLabel = 'Approve', denyLabel = 'Deny', hideResult = false, tone }: ToolCardProps & {
  icon: ReactNode
  title: string
  subject?: string
  /** The card's body. Editable fields while `pending`, read-only after. */
  children: ReactNode
  /** The user's arguments as edited on the card. Return undefined (or equal args) when nothing changed. */
  getEdited?: () => Record<string, unknown> | undefined
  /** A reason Approve is unavailable (e.g. a required field is empty). */
  invalid?: string | null
  approveLabel?: string
  denyLabel?: string
  hideResult?: boolean
  tone?: 'warn'
}): JSX.Element {
  const [busy, setBusy] = useState(false)
  const root = useRef<HTMLDivElement>(null)

  const send = async (approve: boolean): Promise<void> => {
    if (busy || (approve && invalid)) return
    setBusy(true)
    try {
      let edited = approve ? getEdited?.() : undefined
      if (edited && changedKeys(event.arguments, edited).length === 0) edited = undefined
      await decide(approve, edited)
    } finally {
      setBusy(false)
    }
  }

  const edited = wasEdited(event)
  const changed = edited ? changedKeys(event.original_arguments, event.edited_arguments) : []
  return (
    <div
      ref={root}
      className={`tool-event tc-card ${event.pending ? 'pending' : ''} ${pending ? 'awaiting' : ''} ${event.error ? 'error' : ''} ${tone ?? ''}`}
      role="group"
      aria-label={subject ? `${title} ${subject}` : title}
      onKeyDown={(e) => {
        if (pending && e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void send(true) }
      }}
    >
      <header className="tc-head">
        <span className="tc-icon">{icon}</span>
        <span className="tc-title">{title}</span>
        {subject && <span className="tc-subject">{subject}</span>}
        {edited && (
          <span className="tag tc-edited" title={changed.length ? `You changed: ${changed.join(', ')}` : 'You edited the arguments'}>
            <Pencil size={10} /> edited by you
          </span>
        )}
        <StatusChip event={event} />
      </header>
      <div className="tc-body">{children}</div>
      {!hideResult && <ResultBlock event={event} />}
      {pending ? (
        <footer className="tc-foot">
          <button type="button" className="primary-btn sm" disabled={busy || !!invalid} title={invalid ?? 'Approve (⌘↵)'} onClick={() => void send(true)}>
            {approveLabel}
          </button>
          <button type="button" className="ghost-btn sm" disabled={busy} onClick={() => void send(false)}>{denyLabel}</button>
          {invalid ? <span className="tc-hint err">{invalid}</span> : <span className="tc-hint">⌘↵ to approve</span>}
        </footer>
      ) : null}
      {pending && rules}
      <RawDetails event={event as ToolEvent} />
    </div>
  )
}
