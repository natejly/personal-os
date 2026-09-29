import { useEffect, useState } from 'react'
import type { SessionStatus } from '@shared/types'
import { useSessionStatus, useStore } from '../store'

/** The green hold of contract §8, measured here and not in the store so ring and dock tile agree. */
const HOLD_MS = 6000

export interface RingStatus {
  status: SessionStatus
  /** tool calls waiting on the approval card: the count badge on a `needs-approval` ring */
  approvals: number
}

/**
 * The one status source the window ring and the dock tile read, so a minimized chat still shows what
 * it is doing. Pass the window's `ref_id` as-is: unlike `useSessionStatus()` a missing id resolves to
 * `idle` rather than falling back to the focused conversation.
 */
export const useRingStatus = (conversationId: string | null | undefined): RingStatus => {
  const id = conversationId ?? ''
  const status = useSessionStatus(id)
  const approvals = useStore((s) => s.sessions[id]?.pendingApprovals ?? 0)
  const finishedAt = useStore((s) => s.sessions[id]?.finishedAt ?? null)
  const [, tick] = useState(0)
  useEffect(() => {
    if (status !== 'done' || finishedAt === null) return
    const left = finishedAt + HOLD_MS - Date.now()
    if (left <= 0) return
    const t = setTimeout(() => tick((n) => n + 1), left)
    return () => clearTimeout(t)
  }, [status, finishedAt])
  const expired = status === 'done' && finishedAt !== null && Date.now() - finishedAt >= HOLD_MS
  return { status: expired ? 'idle' : status, approvals }
}

export default useRingStatus
