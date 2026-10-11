import { useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, ListChecks } from '../icons'
import type { FullDesk } from '@shared/types'
import { useStore, useConversation, useIsStreaming } from '../store'
import { progressCardShown, progressSource } from '../lib/statusChrome'
import { PlanSteps } from './PlanPanel'
import { MARK } from './DeskPlan'
import ProgressBar from './ProgressBar'

/**
 * Multi-step work, above the composer: "Step 2 of 5", the step in hand and a thin bar, unfolding to the step list. It reads
 * the agent's checklist, or else the decided plan of a desk (lib/statusChrome decides what and when). The checklist's
 * marks can be clicked here as in the side panel; a desk plan's list is read-only.
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
        <ListChecks size={14} />
        <span className="progress-text" role="status" aria-live="polite">
          <b>{title}</b>
          {current && <span className="muted"> · {current}</span>}
        </span>
        <ProgressBar done={done} total={total} label={source.kind === 'plan' ? 'Plan progress' : 'Checklist progress'} />
        <button className="icon-btn ghost" aria-expanded={open} aria-label={open ? 'Hide the steps' : 'Show the steps'} title={open ? 'Hide the steps' : 'Show the steps'}
          onClick={() => setOpenBy((m) => ({ ...m, [cid]: !open }))}>
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </button>
      </div>
      {open && source.kind === 'checklist' && checklist && <PlanSteps cid={cid} steps={checklist} />}
      {open && source.kind === 'plan' && desk?.plan && (
        <ol className="plan-steps progress-plan">
          {desk.plan.steps.filter((s) => s.status !== 'dropped' && s.status !== 'rejected').map((s) => (
            <li key={s.step_id} className={`plan-step ${s.status}`}>
              <span className="desk-mark" title={s.status} aria-label={s.status}>{MARK[s.status]}</span>
              <span className="plan-text">{s.title || s.tool || 'Think it through'}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
