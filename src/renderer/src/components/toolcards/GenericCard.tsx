import type { ToolEvent } from '@shared/types'
import { argRows, changedKeys, describeCall, wasEdited } from '../../lib/toolDisplay'
import ApprovalRules from '../ApprovalRules'
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
 * as a list, and one action row (ApprovalRules): Approve / Deny, a note, and the standing grants.
 */
export function GenericApproval({ event, conversationId, decide }: {
  event: ToolEvent
  conversationId: string
  decide: (approve: boolean) => Promise<void>
}): JSX.Element {
  const d = describeCall(event.name, event.arguments)
  return (
    <div
      className="approval tc-approval"
      role="group"
      aria-label={`Approval needed: ${d.verb}`}
      onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !(e.target as HTMLElement).closest('form, input, textarea')) { e.preventDefault(); void decide(true) } }}
    >
      <div className="approval-text">
        <b>{d.verb}</b>{d.subject ? <> {d.subject}</> : null}. {event.forced
          ? 'This reply read untrusted content, so it needs your OK each time.'
          : 'This acts outside the app.'}
      </div>
      <ArgList rows={argRows(event.arguments)} />
      <ApprovalRules event={event} conversationId={conversationId} decide={decide} />
    </div>
  )
}
