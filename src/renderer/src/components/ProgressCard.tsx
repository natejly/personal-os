import { statusWord } from '../lib/statusWord'
import { useEffect, useState } from 'react'
import { CircleCheck } from '../icons'
import type { FullDesk } from '@shared/types'
import { useStore, useConversation, useIsStreaming } from '../store'
import { progressCardShown, progressSource } from '../lib/statusChrome'
import { PlanSteps } from './PlanSteps'
import { MARK } from './DeskPlan'

/**
 * Multi-step work, above the composer: "Step 2 of 5", and the step in hand, unfolding to the step list. It reads
 * the agent's checklist, or else the decided plan of a desk (lib/statusChrome decides what and when). The checklist's
 * marks can be clicked here; a desk plan's list is read-only.
 */
export default function ProgressCard({ conversationId, desk }: { conversationId?: string; desk?: FullDesk | null }): JSX.Element | null {
  const cid = useConversation(conversationId)?.id
  const checklist = useStore((s) => (cid ? s.plans[cid] : undefined))
  const loadPlan = useStore((s) => s.loadPlan)
  const streaming = useIsStreaming(conversationId)
  const [openBy, setOpenBy] = useState<Record<string, boolean>>({})
  useEffect(() => { if (cid) void loadPlan(cid) }, [cid, loadPlan])

  const source = progressSource(checklist, desk?.plan)
  if (!cid || !source || !progressCardShown(source.summary, streaming)) return null
  const { done, total, current, allDone } = source.summary
  const open = !!openBy[cid]
  const title = allDone ? `All ${total} steps done` : `Step ${Math.min(done + 1, total)} of ${total}`

  return (
    <section className="plan-panel progress-card" aria-label="Progress">
      <div className="progress-row">
        <button className="progress-text" aria-expanded={open} title={open ? 'Hide the steps' : 'Show the steps'}
          onClick={() => setOpenBy((m) => ({ ...m, [cid]: !open }))}>
          <span role="status" aria-live="polite">
            <b>{title}</b>
            {current && <span className="muted"> · {current}</span>}
          </span>
        </button>
      </div>
      {open && source.kind === 'checklist' && checklist && <PlanSteps cid={cid} steps={checklist} />}
      {open && source.kind === 'plan' && desk?.plan && (
        <ol className="plan-steps progress-plan">
          {desk.plan.steps.filter((s) => s.status !== 'dropped' && s.status !== 'rejected').map((s) => (
            <li key={s.step_id} className={`plan-step ${s.status}`}>
              <span className="desk-mark" title={statusWord(s.status)} aria-label={statusWord(s.status)}>{s.status === 'done' ? <CircleCheck size={13} weight="fill" /> : MARK[s.status]}</span>
              <span className="plan-text">{s.title || s.tool || 'Think it through'}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
