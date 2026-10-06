/** Pure helpers for the teach-a-task step editor. The backend normalises again (teach.normalize); this keeps the form honest. */
import type { TeachDraft, TeachStep } from '@shared/types'

export const MAX_STEPS = 15

/** Inputs as editable lines, one `name: example` per line. */
export function formatInputs(inputs: TeachDraft['inputs']): string {
  return inputs.map((i) => (i.example ? `${i.name}: ${i.example}` : i.name)).join('\n')
}

export function parseInputs(text: string): TeachDraft['inputs'] {
  return text.split('\n').map((line) => {
    const at = line.indexOf(':')
    return at < 0 ? { name: line.trim(), example: '' } : { name: line.slice(0, at).trim(), example: line.slice(at + 1).trim() }
  }).filter((i) => i.name)
}

export const blankStep = (n: number): TeachStep => ({ n, app: '', action: '', detail: '', frame: null })

/** Trimmed, steps without an action dropped, renumbered from 1, at most MAX_STEPS. */
export function cleanDraft(d: TeachDraft): TeachDraft {
  const steps = d.steps
    .map((s) => ({ ...s, app: s.app.trim(), action: s.action.trim(), detail: s.detail.trim() }))
    .filter((s) => s.action)
    .slice(0, MAX_STEPS)
    .map((s, i) => ({ ...s, n: i + 1 }))
  return { title: d.title.trim(), goal: d.goal.trim(), inputs: d.inputs.filter((i) => i.name.trim()), steps }
}

/** Move step i by delta (-1 up, +1 down), renumbered. Out-of-range moves return the list unchanged. */
export function moveStep(steps: TeachStep[], i: number, delta: number): TeachStep[] {
  const j = i + delta
  if (i < 0 || j < 0 || i >= steps.length || j >= steps.length) return steps
  const out = [...steps]
  ;[out[i], out[j]] = [out[j], out[i]]
  return out.map((s, k) => ({ ...s, n: k + 1 }))
}

/** k indexes spread evenly over 0..n-1, both ends included (the frame strip; same rule as teach.pick). */
export function pick(n: number, k: number): number[] {
  if (n <= k) return Array.from({ length: n }, (_, i) => i)
  return [...new Set(Array.from({ length: k }, (_, i) => Math.round((i * (n - 1)) / (k - 1))))]
}
