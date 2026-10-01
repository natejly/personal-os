/**
 * The plan-approval card's state, as data.
 *
 * A `propose_plan` call is one approval card for several calls. Approving it pre-authorises exactly the argument
 * values shown, so the card has to show them and the edit has to be exact: the backend re-derives each step's
 * digest from what is sent here, and a step whose arguments do not match asks again on its own. Dropping a step
 * is therefore safe -- it loses its pre-authorisation, nothing more.
 */
import type { PlanEdit, PlanStep, ProposedPlan } from '@shared/types'

/** One row of the card: the proposed step, whether it is kept, and the edited JSON while it is being typed. */
export interface StepDraft {
  idx: number
  tool: string
  why: string
  /** Pretty-printed arguments, as the user may have edited them. */
  text: string
  /** The arguments as proposed, so an untouched step is sent as-is. */
  proposed: Record<string, unknown>
  keep: boolean
}

/** Read a plan out of a propose_plan tool call's arguments. Unknown shapes degrade to an empty plan, not a crash. */
export function readPlan(args: unknown): ProposedPlan {
  const a = (args ?? {}) as Record<string, unknown>
  const raw = Array.isArray(a.steps) ? a.steps : []
  const steps: PlanStep[] = raw.map((s) => {
    const o = (s ?? {}) as Record<string, unknown>
    return {
      tool: typeof o.tool === 'string' ? o.tool : 'unknown',
      arguments: (o.arguments ?? {}) as Record<string, unknown>,
      why: typeof o.why === 'string' ? o.why : ''
    }
  })
  return { title: typeof a.title === 'string' ? a.title : '', steps }
}

export const pretty = (v: unknown): string => JSON.stringify(v ?? {}, null, 2)

export function draftsOf(plan: ProposedPlan): StepDraft[] {
  return plan.steps.map((s, idx) => ({ idx, tool: s.tool, why: s.why ?? '', text: pretty(s.arguments), proposed: s.arguments, keep: true }))
}

/** The arguments a draft stands for, or null when its JSON is not a usable object. */
export function argsOf(d: StepDraft): Record<string, unknown> | null {
  try {
    const v = JSON.parse(d.text)
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null
  } catch {
    return null
  }
}

/** True when a kept step's JSON cannot be parsed: approving must be blocked rather than guessed at. */
export const invalid = (drafts: StepDraft[]): number[] => drafts.filter((d) => d.keep && argsOf(d) === null).map((d) => d.idx)

/** Canonical JSON, keys sorted at every level: the same thing the backend hashes, so key order is not an edit. */
export function canon(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canon).join(',')}]`
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>
    return `{${Object.keys(o).sort().map((k) => `${JSON.stringify(k)}:${canon(o[k])}`).join(',')}}`
  }
  return JSON.stringify(v) ?? 'null'
}

export const edited = (d: StepDraft): boolean => {
  const a = argsOf(d)
  return a === null || canon(a) !== canon(d.proposed)
}

/** Whether the card's state still says exactly what the model proposed, in which case no edit is sent at all. */
export const untouched = (drafts: StepDraft[]): boolean => drafts.every((d) => d.keep && !edited(d))

/**
 * The `steps` payload for POST /approvals: the kept steps by their proposed index, carrying arguments only where
 * the user changed them. Steps left out are dropped. Returns null when nothing was touched.
 */
export function editPayload(drafts: StepDraft[]): PlanEdit[] | null {
  if (untouched(drafts)) return null
  return drafts
    .filter((d) => d.keep)
    .map((d) => {
      const a = argsOf(d)
      return edited(d) && a !== null ? { idx: d.idx, arguments: a } : { idx: d.idx }
    })
}

/** Argument rows for the read-only view: every value shown, strings as themselves rather than as JSON. */
export function argRows(args: Record<string, unknown>): { key: string; value: string }[] {
  return Object.entries(args).map(([key, v]) => ({ key, value: typeof v === 'string' ? v : JSON.stringify(v) }))
}
