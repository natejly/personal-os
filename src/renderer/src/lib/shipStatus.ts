import type { ShipStep, ShipStepStatus } from '@shared/types'

export type ShipTone = 'ok' | 'warn' | 'bad' | 'run' | undefined

const STATUS: Record<ShipStepStatus, { label: string; tone: ShipTone }> = {
  pending: { label: 'Waiting', tone: undefined },
  running: { label: 'Running', tone: 'run' },
  green: { label: 'Passed', tone: 'ok' },
  red: { label: 'Failed', tone: 'bad' },
  skipped: { label: 'Skipped', tone: undefined },
  awaiting_confirm: { label: 'Needs your OK', tone: 'warn' }
}

export const STEP_TITLE: Record<ShipStep['name'], string> = { tests: 'Tests', push: 'Push branch', pr: 'Pull request', merge: 'Merge' }

/** A step's status as the chip's label and badge tone. Unknown values read as waiting. */
export function shipStepChip(status: string): { label: string; tone: ShipTone } {
  return STATUS[status as ShipStepStatus] ?? STATUS.pending
}

/** How long a step ran (or has been running, against `now`), as "12s" / "3m 4s"; '' before it starts. */
export function shipElapsed(step: Pick<ShipStep, 'started_at' | 'ended_at'>, now = Date.now() / 1000): string {
  if (!step.started_at) return ''
  const s = Math.max(0, Math.round((step.ended_at ?? now) - step.started_at))
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`
}
