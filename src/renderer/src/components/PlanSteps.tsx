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
 * The progress card above the composer renders it when unfolded. A finished step's mark is filled.
 */
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
