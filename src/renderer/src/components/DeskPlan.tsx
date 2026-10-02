import { useState } from 'react'
import { ListChecks, Wrench } from 'lucide-react'
import type { FullDesk, PlanStepStatus } from '@shared/types'
import { argRows } from '../lib/planDigest'
import { useStore } from '../store'
import ActionPlanCard from './ActionPlanCard'
import { NoteRow } from './DeskReview'

/**
 * The Plan tab. While the plan is pending this is the approval card itself; once decided it becomes
 * the live checklist — the same marks `ActionPlans.block()` re-injects into the model each round, so
 * what the user reads here is literally what the agent is being held to.
 */

const MARK: Record<PlanStepStatus, string> = {
  proposed: '[ ]',
  approved: '[ ]',
  consumed: '[>]',
  done: '[x]',
  failed: '[!]',
  dropped: '[-]',
  rejected: '[-]'
}

export default function DeskPlan({ desk }: { desk: FullDesk }): JSX.Element {
  const messageDesk = useStore((s) => s.messageDesk)
  const plan = desk.plan
  const [amending, setAmending] = useState(false)

  const amend = (what: string): Promise<boolean> =>
    messageDesk(desk.id, `Stop and re-plan. ${what}\n\nPropose a new plan with propose_plan; do not act on the old one.`)

  if (!plan) {
    return (
      <div className="desk-plan">
        <p className="empty-hint">{desk.status === 'draft' ? 'No plan yet. Start the desk to get one.' : 'No plan yet.'}</p>
      </div>
    )
  }

  if (plan.status === 'pending') {
    return (
      <div className="desk-plan">
        <ActionPlanCard plan={plan} />
      </div>
    )
  }

  const live = plan.steps.filter((s) => s.status !== 'dropped' && s.status !== 'rejected')
  const done = live.filter((s) => s.status === 'done').length

  return (
    <div className="desk-plan">
      <header className="desk-plan-head">
        <ListChecks size={14} />
        <b>{plan.title || 'Plan'}</b>
        <span className="muted small">{done}/{live.length} done · {plan.status}</span>
        <span className="spacer" />
        {!amending && <button className="ghost-btn sm" onClick={() => setAmending(true)}>Amend plan</button>}
      </header>
      {amending && (
        <NoteRow
          label="What should change about the plan? This goes to the agent as a message asking it to re-plan."
          placeholder="Skip the email step and save a draft instead…"
          submitLabel="Ask for a new plan"
          onSubmit={amend}
          onClose={() => setAmending(false)}
        />
      )}
      {plan.intent && <p className="aplan-intent">{plan.intent}</p>}
      {plan.note && <p className="muted small">“{plan.note}”</p>}
      <ol className="desk-checklist">
        {plan.steps.map((s) => (
          <li key={s.step_id} className={s.status}>
            <span className="desk-mark">{MARK[s.status]}</span>
            <div className="desk-check-main">
              <div className="desk-check-head">
                <b>{s.title || s.tool || 'Think it through'}</b>
                {s.tool && <span className={`aplan-tool danger-${s.danger}`}><Wrench size={10} />{s.tool.replace(/_/g, ' ')}</span>}
                {s.edited && <span className="tag">your arguments</span>}
              </div>
              {s.why && <p className="aplan-why">{s.why}</p>}
              {s.tool !== '' && (
                <dl className="aplan-args">
                  {argRows(s.arguments).map((r) => (
                    <div key={r.key} className={r.multiline ? 'wide' : ''}><dt>{r.key}</dt><dd>{r.value}</dd></div>
                  ))}
                </dl>
              )}
              {s.result_error && <p className="aplan-invalid">{s.result_error}</p>}
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}
