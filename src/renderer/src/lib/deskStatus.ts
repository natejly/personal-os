import { useEffect, useState } from 'react'
import type { Desk, DeskAutonomy, DeskStatus } from '@shared/types'

/** Words and clocks for a desk's state, shared by the chat strip, the crew widget and the Agent inbox. */
export const AUTONOMY: { value: DeskAutonomy; label: string; hint: string }[] = [
  { value: 'plan', label: 'Plan first', hint: 'Drafts a plan and waits for you before it touches anything.' },
  { value: 'ask', label: 'Ask as it goes', hint: 'No plan up front; every consequential tool still shows a card.' },
  { value: 'propose', label: 'Work and propose', hint: 'Works in its own folder and brings the result back for review.' }
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

/** How long this desk has been going: wall clock since it was created, frozen once it ended. */
export const deskElapsed = (d: Desk): number => (d.ended_at ?? Date.now() / 1000) - d.created_at

/**
 * A slow re-render while anything is live. Elapsed time is the one number on screen that moves on its
 * own, and `desk_status` only arrives when the agent does something — without this a desk that sits
 * on one tool call for ten minutes keeps claiming "2m".
 */
export function useTick(active: boolean, ms = 30_000): void {
  const [, bump] = useState(0)
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => bump((n) => n + 1), ms)
    return () => clearInterval(t)
  }, [active, ms])
}
