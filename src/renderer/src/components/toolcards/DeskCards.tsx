import { CircleCheck, CircleHelp, PackageCheck, TriangleAlert } from 'lucide-react'
import { gateProblems, num, str, strList } from '../../lib/toolResult'
import CardShell from './CardShell'
import { Badge, Chips, ErrorLine, Meta, unreadable, useParsed } from './blocks'
import { LongText } from './parts'
import { registerToolCard, type ToolCardProps } from './registry'

/** desk_deliver: the file nominated for review, with the backend's placeholder warning when it found one. */
export function DeliverCard(props: ToolCardProps): JSX.Element {
  const { event } = props
  const p = useParsed(event)
  const d = p.data
  const a = event.arguments
  const bytes = num(d?.bytes)
  return (
    <CardShell {...props} icon={<PackageCheck size={14} />} title="Deliver for review" subject={str(a.title) || undefined} hideResult={!unreadable(p, event)}>
      <Meta items={[
        ['File', <span className="mono" key="p">{str(d?.path) || str(a.path)}</span>],
        ['Size', bytes !== null ? (bytes < 1024 ? `${bytes} bytes` : `${Math.round(bytes / 1024).toLocaleString()} KB`) : null]
      ]} />
      {str(a.summary) && <LongText text={str(a.summary)} />}
      {d && str(d.warning) && <div className="tc-hint warn tc-warning"><TriangleAlert size={12} /> {str(d.warning)}</div>}
      {d && !event.error && <div className="tc-muted">Waiting in the Files tab for your review.</div>}
      <ErrorLine event={event} />
    </CardShell>
  )
}

/** desk_done: the summary; when the completion gate refused, the open problems as a list under "Not finished yet". */
export function DoneCard(props: ToolCardProps): JSX.Element {
  const { event } = props
  const p = useParsed(event)
  const a = event.arguments
  const gate = gateProblems(event.error)
  return (
    <CardShell {...props} icon={gate ? <TriangleAlert size={14} /> : <CircleCheck size={14} />} title={gate ? 'Not finished yet' : 'Finish desk task'}
      tone={gate ? 'warn' : undefined} hideResult={!unreadable(p, event)}>
      {str(a.summary) && <LongText text={str(a.summary)} />}
      {gate ? (
        <div className="tc-section">
          <div className="tc-muted">The completion check turned this back{gate.lead ? `: ${gate.lead}` : ''}.</div>
          {gate.problems.length > 0 && <ol className="tc-list tc-problems">{gate.problems.map((x, i) => <li key={i}>{x}</li>)}</ol>}
        </div>
      ) : <ErrorLine event={event} />}
      {!event.error && p.data && num(p.data.outputs_awaiting_review) !== null && <Meta items={[['Awaiting your review', String(num(p.data.outputs_awaiting_review))]]} />}
      {str(a.next_steps) && <Meta items={[['Next steps', str(a.next_steps)]]} />}
    </CardShell>
  )
}

/**
 * desk_ask / ask_user once it is no longer waiting on an answer (ToolEvents keeps the answer box for the pending one):
 * the question, the offered choices as chips, and the answer, with the chosen chip marked.
 */
export function AskCard(props: ToolCardProps): JSX.Element {
  const { event } = props
  const p = useParsed(event)
  const d = p.data
  const a = event.arguments
  const options = strList(a.options).length ? strList(a.options) : d ? strList(d.options) : []
  const answer = d ? str(d.answer) : ''
  const answered = d?.status === 'answered' || !!answer
  return (
    <CardShell {...props} icon={<CircleHelp size={14} />} title="Asked you" hideResult={!unreadable(p, event)}>
      <p className="tc-sentence">{str(a.question)}</p>
      {str(a.context) && <div className="tc-muted">{str(a.context)}</div>}
      <Chips items={options} chosen={str(d?.choice) || undefined} />
      {answered ? (
        <div className="tc-statusrow"><Badge tone="ok">Your answer</Badge><span>{answer || 'Approved without a typed answer'}</span></div>
      ) : d?.status === 'no_answer' ? <div className="tc-muted">Approved without a typed answer.</div>
        : d ? <div className="tc-muted">Waiting for your answer{event.name === 'desk_ask' ? ' in Needs you' : ''}.</div> : null}
      <ErrorLine event={event} />
    </CardShell>
  )
}

registerToolCard('desk_deliver', DeliverCard)
registerToolCard('desk_done', DoneCard)
registerToolCard('desk_ask', AskCard)
registerToolCard('ask_user', AskCard)
