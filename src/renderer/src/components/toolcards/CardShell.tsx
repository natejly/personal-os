import { useId, useRef, useState, type ReactNode } from 'react'
import { ChevronRight, Pencil } from '../../icons'
import type { ToolEvent } from '@shared/types'
import { changedKeys, describeCall, fmtMs, wasEdited } from '../../lib/toolDisplay'
import { RawDetails, ResultBlock, StatusChip, StatusDot } from './parts'
import type { ToolCardProps } from './registry'
import './toolcards.css'
import { SettleWash } from '../SettleWash'

/**
 * The chrome every dedicated card shares: header (icon, title, subject, status), the card's own body, the result,
 * a Details disclosure for the raw call, and Approve / Deny while the call waits, with the "Always allow" menu
 * (`rules`) as the third button of the same row. Once decided the footer is gone and the status chip is the record.
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
  // One line until opened. A call waiting on the user is always open, and a failed one opens by itself.
  const [manual, setManual] = useState<boolean | null>(null)
  const bodyId = useId()
  const open = pending || (manual ?? !!event.error)
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
      className={`tool-event tc-card ${event.pending ? 'pending' : ''} ${pending ? 'awaiting' : ''} ${event.error ? 'error' : ''} ${open ? '' : 'collapsed'} ${tone ?? ''}`}
      role="group"
      aria-label={subject ? `${title} ${subject}` : title}
      onKeyDown={(e) => {
        if (pending && e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void send(true) }
      }}
    >
      <SettleWash awaiting={!!pending} denied={event.approval === 'deny'} />
      <header className={`tc-head ${open ? 'open' : ''}`}>
        {(() => {
          const line = (
            <>
              {!pending && <ChevronRight size={12} className={`tc-chev ${open ? 'rot90' : ''}`} />}
              <StatusDot event={event} />
              <span className="tc-icon">{icon}</span>
              <span className="tc-title">{title}</span>
              {(subject || !open) && <span className="tc-subject">{subject ?? describeCall(event.name, event.arguments).subject}</span>}
              {edited && (
                <span className="tag tc-edited" title={changed.length ? `You changed: ${changed.join(', ')}` : 'You edited the arguments'}>
                  <Pencil size={10} /> edited by you
                </span>
              )}
              <StatusChip event={event} />
              {!event.pending && event.duration_ms > 0 && <span className="tool-ms">{fmtMs(event.duration_ms)}</span>}
            </>
          )
          return pending
            ? <div className="tc-toggle" data-testid="tool-row">{line}</div>
            : <button type="button" className="tc-toggle" data-testid="tool-row" aria-expanded={open} aria-controls={bodyId} onClick={() => setManual(!open)}>{line}</button>
        })()}
      </header>
      {open && <div id={bodyId} className="tc-panel">
      <div className="tc-body">{children}</div>
      {!hideResult && <ResultBlock event={event} />}
      {pending ? (
        <footer className="tc-foot">
          <button type="button" className="primary-btn sm" data-testid="approval-approve" disabled={busy || !!invalid} title={invalid ?? 'Approve (⌘↵)'} onClick={() => void send(true)}>
            {approveLabel}
          </button>
          <button type="button" className="ghost-btn sm" data-testid="approval-deny" disabled={busy} onClick={() => void send(false)}>{denyLabel}</button>
          {rules}
          {invalid && <span className="tc-hint err">{invalid}</span>}
        </footer>
      ) : null}
      <RawDetails event={event as ToolEvent} />
      </div>}
    </div>
  )
}
