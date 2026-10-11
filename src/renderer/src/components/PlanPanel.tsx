import { useEffect } from 'react'
import { Circle, CircleDot, CheckCircle2 } from '../icons'
import { useStore, useConversation } from '../store'
import type { PlanStep } from '@shared/types'
import { planSummary } from '../lib/plan'
import ProgressBar from './ProgressBar'

const NEXT: Record<PlanStep['status'], PlanStep['status']> = { pending: 'in_progress', in_progress: 'done', done: 'pending' }
const ICON: Record<PlanStep['status'], JSX.Element> = {
  pending: <Circle size={13} />,
  in_progress: <CircleDot size={13} />,
  done: <CheckCircle2 size={13} />
}

/** The checklist's steps; clicking a mark cycles that step's status. Shared by the side panel and the progress card. */
export function PlanSteps({ cid, steps }: { cid: string; steps: PlanStep[] }): JSX.Element {
  const setPlanSteps = useStore((s) => s.setPlanSteps)
  const cycle = (i: number): void =>
    void setPlanSteps(cid, steps.map((s, j) => (i === j ? { ...s, status: NEXT[s.status] } : s)))
  return (
    <ol className="plan-steps">
      {steps.map((s, i) => (
        <li key={s.id} className={`plan-step ${s.status}`}>
          <button className="plan-check" title="pending → in progress → done" aria-label={`${s.text}: ${s.status.replace('_', ' ')}`} onClick={() => cycle(i)}>
            {ICON[s.status]}
          </button>
          <span className="plan-text">
            {s.text}
            {s.note && <small>{s.note}</small>}
          </span>
        </li>
      ))}
    </ol>
  )
}

/**
 * The chat's plan artifact, live. The assistant writes it with `todo_write` and the backend re-sends
 * it at the end of every round, so this panel is the same object the model is working from — ticking a
 * step here is read by the model on its next round, not just a note to yourself.
 * Renders nothing until a plan exists. It lives in the chat's side panel (Checklist tab); a multi-step checklist also gets
 * a compact progress card above the composer (ProgressCard, lib/statusChrome).
 */
export default function PlanPanel({ conversationId }: { conversationId?: string }): JSX.Element | null {
  const convo = useConversation(conversationId)
  const cid = convo?.id
  const steps = useStore((s) => (cid ? s.plans[cid] : undefined))
  const { loadPlan, clearPlan } = useStore()
  const { done, total } = planSummary(steps ?? [])

  useEffect(() => {
    if (cid) void loadPlan(cid)
  }, [cid, loadPlan])

  if (!cid || !steps?.length) return null

  return (
    <section className="plan-panel" aria-label="Checklist">
      <header>
        <span className="plan-count">{done}/{total} done</span>
        <span className="spacer" />
        <button className="ghost-btn xs" title="Clear the checklist" aria-label="Clear the checklist" onClick={() => void clearPlan(cid)}>Clear</button>
      </header>
      <ProgressBar done={done} total={total} label="Checklist progress" />
      <PlanSteps cid={cid} steps={steps} />
    </section>
  )
}
