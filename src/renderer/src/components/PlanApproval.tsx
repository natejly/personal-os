import { useMemo, useState } from 'react'
import { ListChecks, Pencil, AlertCircle } from 'lucide-react'
import type { ToolEvent } from '@shared/types'
import { useStore } from '../store'
import { argRows, argsOf, draftsOf, edited, editPayload, invalid, pretty, readPlan, type StepDraft } from '../lib/planSteps'

/**
 * One card for a whole plan, instead of one modal per call.
 *
 * The point is oversight, not fewer clicks: every step's real arguments are on the card, because approving binds
 * the authorisation to exactly those values. Editing a step re-derives what is authorised, so the values here are
 * the only ones that will run without asking again.
 */
export default function PlanApproval({ event, conversationId }: { event: ToolEvent; conversationId: string }): JSX.Element {
  const approveTool = useStore((s) => s.approveTool)
  const plan = useMemo(() => readPlan(event.arguments), [event.arguments])
  const [drafts, setDrafts] = useState<StepDraft[]>(() => draftsOf(plan))
  const [editing, setEditing] = useState<Record<number, boolean>>({})
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const bad = invalid(drafts)
  const kept = drafts.filter((d) => d.keep)
  const patch = (idx: number, p: Partial<StepDraft>): void => setDrafts((ds) => ds.map((d) => (d.idx === idx ? { ...d, ...p } : d)))

  const send = async (decision: 'allow' | 'deny'): Promise<void> => {
    setBusy(true)
    try {
      await approveTool(event.id, decision, conversationId, {
        steps: decision === 'allow' ? editPayload(drafts) : null,
        note: note.trim() || undefined
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="approval plan-approval">
      <div className="plan-head">
        <ListChecks size={14} />
        <b>{plan.title || 'Plan'}</b>
        <span className="plan-count">{plan.steps.length} {plan.steps.length === 1 ? 'action' : 'actions'}</span>
      </div>
      <div className="approval-text">
        Approve once, and only these arguments run. Change or remove anything you are not happy with — a step that
        does not match what you approve asks you again on its own.
      </div>
      {event.forced && <div className="plan-warn"><AlertCircle size={12} /> This chat has read untrusted content: read the arguments closely.</div>}
      <ol className="plan-steps">
        {drafts.map((d) => {
          const args = argsOf(d)
          const rows = args ? argRows(args) : []
          return (
            <li key={d.idx} className={`plan-step ${d.keep ? '' : 'dropped'}`}>
              <div className="plan-step-head">
                <label className="plan-keep">
                  <input type="checkbox" checked={d.keep} onChange={(e) => patch(d.idx, { keep: e.target.checked })} aria-label={`Approve step ${d.idx + 1}: ${d.tool}`} />
                  <span className="plan-tool">{d.tool.replace(/_/g, ' ')}</span>
                </label>
                {edited(d) && d.keep && <span className="tag ask">edited</span>}
                <button className="ghost-btn sm" onClick={() => setEditing((o) => ({ ...o, [d.idx]: !o[d.idx] }))} title="Edit this step's arguments">
                  <Pencil size={11} /> {editing[d.idx] ? 'done' : 'edit'}
                </button>
              </div>
              {d.why && <div className="plan-why">{d.why}</div>}
              {editing[d.idx] ? (
                <>
                  <textarea className="plan-edit" rows={Math.min(14, d.text.split('\n').length + 1)} value={d.text}
                            onChange={(e) => patch(d.idx, { text: e.target.value })} spellCheck={false}
                            aria-label={`Arguments for step ${d.idx + 1}`} />
                  {args === null && <div className="plan-warn"><AlertCircle size={12} /> Not valid JSON yet.</div>}
                  {args !== null && edited(d) && (
                    <button className="ghost-btn sm" onClick={() => patch(d.idx, { text: pretty(d.proposed) })}>revert</button>
                  )}
                </>
              ) : (
                <dl className="plan-args">
                  {rows.length === 0 && <div className="plan-empty">no arguments</div>}
                  {rows.map((r) => (
                    <div key={r.key} className="plan-arg">
                      <dt>{r.key}</dt>
                      <dd>{r.value}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </li>
          )
        })}
      </ol>
      <input className="plan-note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Optional note back to the assistant" aria-label="Note to the assistant" />
      <div className="approval-actions">
        <button className="primary-btn sm" disabled={busy || bad.length > 0 || kept.length === 0} onClick={() => void send('allow')}>
          {kept.length === plan.steps.length ? `Approve all (${plan.steps.length})` : `Approve ${kept.length} of ${plan.steps.length}`}
        </button>
        <button className="ghost-btn sm" disabled={busy} onClick={() => void send('deny')}>Deny</button>
        {bad.length > 0 && <span className="plan-warn">step {bad.map((i) => i + 1).join(', ')}: fix the JSON first</span>}
        {bad.length === 0 && kept.length === 0 && <span className="plan-warn">nothing left to approve — deny the plan instead</span>}
      </div>
    </div>
  )
}
