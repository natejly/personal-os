import { useState } from 'react'
import type { ToolEvent } from '@shared/types'
import { argRows, changedKeys, describeCall, wasEdited } from '../../lib/toolDisplay'
import { ArgList, RawDetails, ResultBlock } from './parts'
import './toolcards.css'

/**
 * The expanded body of any tool row that has no dedicated card: what it was asked (labelled, long values
 * collapsed), what came back (readable), and the raw call only behind "Details".
 */
export function GenericBody({ event }: { event: ToolEvent }): JSX.Element {
  const edited = wasEdited(event)
  const changed = edited ? changedKeys(event.original_arguments, event.edited_arguments) : []
  return (
    <div className="tc-generic">
      <ArgList rows={argRows(event.arguments)} highlight={changed} />
      <ResultBlock event={event} />
      <RawDetails event={event} />
    </div>
  )
}

/**
 * The approval for a call with no dedicated card: a plain-language statement of what will happen, the arguments
 * as a list, and Approve / Deny, with the standing grants the old modal had. A forced approval (untrusted content
 * in this reply) offers no standing grant: the backend would downgrade it to one-shot anyway.
 */
export function GenericApproval({ event, decide, grant, onWhy }: {
  event: ToolEvent
  decide: (approve: boolean) => Promise<void>
  grant: (d: 'always_chat' | 'always_global') => Promise<void>
  /** Opens the context panel (it lists the taint sources and has Clear); only where that panel shows this chat. */
  onWhy?: () => void
}): JSX.Element {
  const [busy, setBusy] = useState(false)
  const d = describeCall(event.name, event.arguments)
  const run = (fn: () => Promise<void>) => async (): Promise<void> => {
    if (busy) return
    setBusy(true)
    try { await fn() } finally { setBusy(false) }
  }
  return (
    <div
      className="approval tc-approval"
      role="group"
      aria-label={`Approval needed: ${d.verb}`}
      onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void run(() => decide(true))() } }}
    >
      <div className="approval-text">
        <b>{d.verb}</b>{d.subject ? <> {d.subject}</> : null}. {event.forced
          ? <>This chat has read untrusted content, so this needs your OK each time.
            {onWhy && <>{' '}<button type="button" className="link small" onClick={onWhy}>See why</button></>}</>
          : 'This acts outside the app.'}
      </div>
      <ArgList rows={argRows(event.arguments)} />
      <div className="approval-actions">
        <button type="button" className="primary-btn" disabled={busy} title="Approve (⌘↵)" onClick={() => void run(() => decide(true))()}>Approve</button>
        <button type="button" className="ghost-btn danger" disabled={busy} onClick={() => void run(() => decide(false))()}>Deny</button>
        {!event.forced && event.permission?.danger !== 'external' && (
          <>
            <button type="button" className="ghost-btn" disabled={busy} onClick={() => void run(() => grant('always_chat'))()}>Always in this chat</button>
            <button type="button" className="ghost-btn" disabled={busy} onClick={() => void run(() => grant('always_global'))()}>Always</button>
          </>
        )}
      </div>
    </div>
  )
}
