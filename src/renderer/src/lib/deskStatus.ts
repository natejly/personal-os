import type { Desk, DeskAutonomy, DeskStatus } from '@shared/types'

/** Words and clocks for a desk's state, shared by the chat strip, the crew widget and the Agent inbox. */
export const AUTONOMY: { value: DeskAutonomy; label: string; hint: string }[] = [
  { value: 'plan', label: 'Plan first', hint: 'Writes a plan for you to approve, then carries out the approved steps.' },
  { value: 'ask', label: 'Ask as it goes', hint: 'Acts directly on your files as it works, asking when something needs you.' },
  { value: 'propose', label: 'Autonomous', hint: 'Works in its own folder and brings back the changes for review before applying them.' }
]

export const STATUS_LABEL: Record<DeskStatus, string> = {
  draft: 'Draft',
  planning: 'Planning',
  awaiting_plan: 'Plan to approve',
  working: 'Working',
  needs_approval: 'Approval needed',
  blocked: 'Waiting on you',
  paused: 'Paused',
  interrupted: 'Interrupted',
  review: 'Ready to review',
  done: 'Done',
  failed: 'Failed',
  stopped: 'Stopped',
  queued: 'Queued'
}


export const fmtDur = (seconds: number): string => {
  const s = Math.max(0, seconds)
  if (s < 60) return `${Math.round(s)}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`
  return `${Math.round(s / 86400)}d`
}
