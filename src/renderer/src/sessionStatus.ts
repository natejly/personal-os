import type { ChatEvent, SessionStatus } from '@shared/types'

/** The SessionStatus machine of the canvas contract §8, pure so it can be tested without a store. */

/**
 * Approval cards resolving. `pendingApprovals` is the count still waiting *after* whatever just
 * happened, so `approveTool` can call this optimistically without an event.
 */
export const settleApprovals = (prev: SessionStatus, pendingApprovals: number): SessionStatus =>
  pendingApprovals > 0 ? 'needs-approval' : prev === 'needs-approval' ? 'working' : prev

export const reduceStatus = (prev: SessionStatus, ev: ChatEvent, pendingApprovals: number): SessionStatus => {
  switch (ev.event) {
    case 'done':
      // `stopped` is not a failure: a partial answer still counts as done (contract §12.3).
      return ev.data.error ? 'error' : 'done'
    case 'error':
      return 'error'
    case 'tool_call':
    case 'tool_result':
      return settleApprovals(prev === 'idle' ? 'working' : prev, pendingApprovals)
    // Auto-learn spans and their toasts arrive *after* `done`; reacting would resurrect `working`.
    case 'span':
    case 'learned':
    case 'learn_error':
      return prev
    default:
      return prev === 'idle' ? 'working' : prev
  }
}

/** `finally` in runStream: an interrupted run falls back to idle, a finished one keeps its verdict. */
export const finishStatus = (prev: SessionStatus): SessionStatus =>
  prev === 'done' || prev === 'error' ? prev : 'idle'
