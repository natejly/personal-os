import { useEffect, useState } from 'react'
import { ListChecks, ChevronDown, ChevronRight, Circle, CircleDot, CheckCircle2, X } from 'lucide-react'
import { useStore, useConversation } from '../store'
import type { PlanStep } from '@shared/types'

const NEXT: Record<PlanStep['status'], PlanStep['status']> = { pending: 'in_progress', in_progress: 'done', done: 'pending' }
const ICON: Record<PlanStep['status'], JSX.Element> = {
  pending: <Circle size={13} />,
  in_progress: <CircleDot size={13} />,
  done: <CheckCircle2 size={13} />
}

/**
 * The chat's plan artifact, live. The assistant writes it with `todo_write` and the backend re-sends
 * it at the end of every round, so this panel is the same object the model is working from — ticking a
 * step here is read by the model on its next round, not just a note to yourself.
 * Renders nothing until a plan exists, which keeps a short chat visually unchanged.
 */
export default function PlanPanel({ conversationId }: { conversationId?: string }): JSX.Element | null {
  const convo = useConversation(conversationId)
  const cid = convo?.id
  const steps = useStore((s) => (cid ? s.plans[cid] : undefined))
  const { loadPlan, setPlanSteps, clearPlan } = useStore()
  const [open, setOpen] = useState(true)

  useEffect(() => {
    if (cid) void loadPlan(cid)
  }, [cid, loadPlan])

  if (!cid || !steps?.length) return null
  const done = steps.filter((s) => s.status === 'done').length
  const cycle = (i: number): void =>
    void setPlanSteps(cid, steps.map((s, j) => (i === j ? { ...s, status: NEXT[s.status] } : s)))

  return (
    <section className="plan-panel" aria-label="Checklist">
      <header>
        <button className="plan-head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          <ListChecks size={14} />
          <b>Checklist</b>
          <span className="muted">{done}/{steps.length}</span>
        </button>
        <div className="plan-bar" aria-hidden><span style={{ width: `${Math.round((done / steps.length) * 100)}%` }} /></div>
        <button className="icon-btn" title="Clear the checklist" aria-label="Clear the checklist" onClick={() => void clearPlan(cid)}><X size={14} /></button>
      </header>
      {open && (
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
      )}
    </section>
  )
}
