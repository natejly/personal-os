import type { ChatEvent, Conversation, Message, RunInfo, SessionStatus } from '@shared/types'

/** The session logic of the canvas contract §8 — status machine, LRU and merge — pure so it can be tested without a store. */

/**
 * Approval cards resolving. `pendingApprovals` is the count still waiting *after* whatever just
 * happened, so `approveTool` can call this optimistically without an event.
 */
export const settleApprovals = (prev: SessionStatus, pendingApprovals: number): SessionStatus =>
  pendingApprovals > 0 ? 'needs-approval' : prev === 'needs-approval' ? 'working' : prev

export const reduceStatus = (prev: SessionStatus, ev: ChatEvent, pendingApprovals: number): SessionStatus => {
  switch (ev.event) {
    case 'done':
      // A steer segment closing is not the end of the run: the next segment is already coming.
      if (ev.data.segment && !ev.data.error) return prev
      // `stopped` is not a failure: a partial answer still counts as done (contract §12.3).
      return ev.data.error ? 'error' : 'done'
    case 'error':
      return 'error'
    case 'tool_call':
    case 'tool_result':
      return settleApprovals(prev === 'idle' ? 'working' : prev, pendingApprovals)
    // A steered run closes one reply segment (`done`) and streams the next; these events mean it is
    // alive again, so the green verdict yields back to amber.
    case 'user_message':
    case 'assistant_message':
    case 'delta':
    case 'reasoning':
      return prev === 'idle' || prev === 'done' ? 'working' : prev
    // A `remember` tool's toast can land beside `done`, and a trailing span after it; reacting to
    // either would resurrect `working`. (Auto-learn itself reports on `/events`, not here.)
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

/** Just enough of a session for the LRU to rank it; `ChatSession` satisfies it structurally. */
export interface EvictCandidate { streaming: unknown; unread: number; touchedAt: number }

/**
 * Victims for the session LRU, least recently touched first. Off limits: anything in `keep` (the
 * focused chat plus every conversation with a mounted window), a live run, and unread replies.
 * Canvas mode never focuses a session, so `keep` — not `touchedAt` — is what stops an on-screen
 * window being evicted out from under its loader.
 */
export const pickEvictions = (sessions: Record<string, EvictCandidate>, keep: ReadonlySet<string>, max: number): string[] => {
  const ids = Object.keys(sessions)
  if (ids.length <= max) return []
  return ids
    .filter((id) => !keep.has(id) && !sessions[id].streaming && !sessions[id].unread)
    .sort((a, b) => sessions[a].touchedAt - sessions[b].touchedAt)
    .slice(0, ids.length - max)
}

/** Server row wins per field, except where the live stream holds more than the server has persisted. */
const longerText = (remote?: string | null, local?: string | null): string | null =>
  (remote?.length ?? 0) >= (local?.length ?? 0) ? (remote ?? null) : (local ?? null)

const mergeMessage = (local: Message, remote: Message): Message => ({
  ...remote,
  // An error stamped by the run's terminal event is not on the row a stale fetch returns.
  error: remote.error ?? local.error,
  outcome: remote.outcome ?? local.outcome ?? null,
  error_kind: remote.error_kind ?? local.error_kind ?? null,
  content: remote.content.length >= local.content.length ? remote.content : local.content,
  tool_events: remote.tool_events?.length ? remote.tool_events : local.tool_events,
  trace: remote.trace?.length ? remote.trace : local.trace,
  reasoning: longerText(remote.reasoning, local.reasoning)
})

/**
 * Fold a freshly fetched conversation into the one a session already holds. Replacing it wholesale
 * discards what a stream applied, and a fetch can be older than the deltas it lands among — message
 * content only ever grows, so the longer side wins per id.
 *
 * `keepUnsent` (the session is streaming) also keeps messages the server has not stored yet. Without
 * it the remote list is authoritative, so a message deleted server-side — `removed_message` from a
 * regenerate — stays deleted instead of being resurrected from the local copy.
 */
export const mergeConversation = (local: Conversation, remote: Conversation, keepUnsent: boolean): Conversation => {
  const unseen = new Map((local.messages ?? []).map((m) => [m.id, m]))
  const messages = (remote.messages ?? []).map((r) => {
    const l = unseen.get(r.id)
    if (!l) return r
    unseen.delete(r.id)
    return mergeMessage(l, r)
  })
  return { ...remote, messages: keepUnsent ? [...messages, ...unseen.values()] : messages }
}

/**
 * Where an attach replays from. With an assistant message on the tape, the event just before it, so the
 * whole in-flight message is rebuilt from its own deltas; before one exists, the tape is replayed as is.
 */
export const replayCursor = (run: Pick<RunInfo, 'seq' | 'message_seq'>): number =>
  run.message_seq != null ? run.message_seq - 1 : run.seq

/** Live runs by conversation, kept current from `run_state` frames. A stale end for another run leaves the entry alone. */
export type LiveRuns = Record<string, { run_id: string; status?: string }>

export const foldRunState = (map: LiveRuns, info: RunInfo): LiveRuns => {
  if (info.answering) return { ...map, [info.conversation_id]: { run_id: info.run_id, status: info.status } }
  if (map[info.conversation_id]?.run_id !== info.run_id) return map
  const { [info.conversation_id]: _gone, ...rest } = map
  return rest
}

/** The sidebar pulse: a session's own status wins, and a conversation with no session falls back to its live run. */
export const pulseStatus = (sessionStatus: SessionStatus, liveRun?: { status?: string } | null): SessionStatus => {
  if (sessionStatus !== 'idle') return sessionStatus
  if (!liveRun) return 'idle'
  return liveRun.status === 'awaiting_approval' ? 'needs-approval' : 'working'
}
