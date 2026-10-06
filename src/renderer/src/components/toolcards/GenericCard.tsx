import type { ToolEvent } from '@shared/types'
import { argRows, changedKeys, describeCall, wasEdited } from '../../lib/toolDisplay'
import ApprovalRules from '../ApprovalRules'
import { ArgList, RawDetails, ResultBlock } from './parts'
import { useStore } from '../../store'
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
export function GenericApproval({ event, conversationId, decide, onWhy }: {
  event: ToolEvent
  conversationId: string
  decide: (approve: boolean) => Promise<void>
  /** Opens the context panel (it lists the taint sources and has Clear); only where that panel shows this chat. */
  onWhy?: () => void
}): JSX.Element {
  const d = describeCall(event.name, event.arguments)
  // A doc tool names its doc by id; show the title the user knows it by.
  const docTitle = useStore((s) => s.docs.find((x) => x.id === event.arguments?.doc)?.title)
  const args = docTitle ? { ...event.arguments, doc: docTitle } : event.arguments
  return (
    <div
      className="approval tc-approval"
      role="group"
      aria-label={`Approval needed: ${d.verb}`}
      onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !(e.target as HTMLElement).closest('form, input, textarea')) { e.preventDefault(); void decide(true) } }}
    >
      <div className="approval-text">
        <b>{d.verb}</b>{d.subject ? <> {d.subject}</> : null}. {event.forced
          ? <>This chat has read untrusted content, so this needs your OK each time.
            {onWhy && <>{' '}<button type="button" className="link small" onClick={onWhy}>See why</button></>}</>
          : 'This acts outside the app.'}
      </div>
      <ArgList rows={argRows(args)} />
      <ApprovalRules event={event} conversationId={conversationId} decide={decide} />
    </div>
  )
}
