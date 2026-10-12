import { useState } from 'react'
import { Circle, CircleDot, CheckCircle2 } from '../icons'
import { useStore } from '../store'
import type { PlanStep } from '@shared/types'

const NEXT: Record<PlanStep['status'], PlanStep['status']> = { pending: 'in_progress', in_progress: 'done', done: 'pending' }
const ICON: Record<PlanStep['status'], JSX.Element> = {
  pending: <Circle size={13} />,
  in_progress: <CircleDot size={13} />,
  done: <CheckCircle2 size={13} weight="fill" />
}

/**
 * A checklist's steps, one per row; clicking a mark cycles that step's status (pending, in progress, done).
 * The progress card above the composer renders it when unfolded. More than 2 finished steps fold into an N-done row.
 */
export function PlanSteps({ cid, steps }: { cid: string; steps: PlanStep[] }): JSX.Element {
  const setPlanSteps = useStore((s) => s.setPlanSteps)
  const [all, setAll] = useState(false)
  const cycle = (i: number): void =>
    void setPlanSteps(cid, steps.map((s, j) => (i === j ? { ...s, status: NEXT[s.status] } : s)))
  // More than 2 finished: fold all but the last behind one "N done" row.
  const doneIdx = steps.flatMap((s, i) => (s.status === 'done' ? [i] : []))
  const hidden = !all && doneIdx.length > 2 ? new Set(doneIdx.slice(0, -1)) : null
  return (
    <ol className="plan-steps">
      {hidden && (
        <li className="plan-more">
          <button onClick={() => setAll(true)} aria-expanded={false}>{hidden.size} done</button>
        </li>
      )}
      {steps.map((s, i) => hidden?.has(i) ? null : (
        <li key={s.id} className={`plan-step ${s.status}`}>
          <button className="plan-check" title="pending → in progress → done" aria-label={`${s.text}: ${s.status.replace('_', ' ')}`} onClick={() => cycle(i)}>
            {ICON[s.status]}
          </button>
          <span className="plan-text" title={s.text}>
            <span className="plan-title">{s.text}</span>
            {s.note && <small>{s.note}</small>}
          </span>
        </li>
      ))}
    </ol>
  )
}
