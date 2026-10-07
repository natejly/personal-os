import type { PlanStep } from '@shared/types'

/** What the collapsed checklist pill shows: progress and the step being worked on. */
export function planSummary(steps: Pick<PlanStep, 'text' | 'status'>[]): { done: number; total: number; current: string | null; allDone: boolean } {
  const done = steps.filter((s) => s.status === 'done').length
  const open = steps.find((s) => s.status === 'in_progress') ?? steps.find((s) => s.status === 'pending')
  return { done, total: steps.length, current: open?.text ?? null, allDone: steps.length > 0 && done === steps.length }
}
