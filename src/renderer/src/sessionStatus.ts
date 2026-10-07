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

const mergeMessage = (local: Message, remote: Message, live = false): Message => ({
  ...remote,
  // An error stamped by the run's terminal event is not on the row a stale fetch returns.
  error: remote.error ?? local.error,
  outcome: remote.outcome ?? local.outcome ?? null,
  error_kind: remote.error_kind ?? local.error_kind ?? null,
  // The reply a stream is still filling is the stream's: it appends every later delta to what is held, so taking a longer
  // stored copy here would let the deltas it has not applied yet land on text that already holds them.
  content: live || local.content.length > remote.content.length ? local.content : remote.content,
  tool_events: remote.tool_events?.length ? remote.tool_events : local.tool_events,
  trace: remote.trace?.length ? remote.trace : local.trace,
  reasoning: live ? (local.reasoning ?? null) : longerText(remote.reasoning, local.reasoning)
})

/**
 * Fold a freshly fetched conversation into the one a session already holds. Replacing it wholesale
 * discards what a stream applied, and a fetch can be older than the deltas it lands among — message
 * content only ever grows, so the longer side wins per id.
 *
 * `liveMessageId` is the reply a watcher is still filling: its text and reasoning stay as streamed, whatever the fetch holds.
 *
 * `keepUnsent` (the session is streaming) also keeps messages the server has not stored yet. Without
 * it the remote list is authoritative, so a message deleted server-side — `removed_message` from a
 * regenerate — stays deleted instead of being resurrected from the local copy.
 */
export const mergeConversation = (local: Conversation, remote: Conversation, keepUnsent: boolean, liveMessageId?: string | null): Conversation => {
  const unseen = new Map((local.messages ?? []).map((m) => [m.id, m]))
  const messages = (remote.messages ?? []).map((r) => {
    const l = unseen.get(r.id)
    if (!l) return r
    unseen.delete(r.id)
    return mergeMessage(l, r, r.id === liveMessageId)
  })
  // The row is only stored as untrusted when the run ends, so a fetch mid-reply must not clear what the stream has seen.
  const settings = keepUnsent && local.settings.tainted && !remote.settings.tainted
    ? { ...remote.settings, tainted: true, taint_sources: local.settings.taint_sources }
    : remote.settings
  return { ...remote, settings, messages: keepUnsent ? [...messages, ...unseen.values()] : messages }
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

/**
 * Whether a conversation is in front of the user: the chat view showing it, or any surface that has it
 * mounted (a canvas window or a pop-out holds a `retained` pin for as long as it does).
 */
export const onScreen = (convId: string, where: { view: string; focusedId: string | null; retained: { has: (id: string) => boolean } }): boolean =>
  (where.view === 'chat' && where.focusedId === convId) || where.retained.has(convId)

/**
 * What a `run_state` frame for a reply this window did not start asks of a loaded session. A stream holds one
 * of the renderer's six connections to the backend, so only a session on screen follows a live run; one off
 * screen reads what the run persisted once it ends.
 */
export const followRun = (streaming: { runId: string } | null, info: Pick<RunInfo, 'run_id' | 'answering'>, visible: boolean): 'attach' | 'open' | null => {
  if (streaming?.runId === info.run_id) return null
  if (info.answering) return visible ? 'attach' : null
  return streaming ? null : 'open'
}

export type ChatNoticeKind = 'reply' | 'approval' | 'failed'

/**
 * What an event just did to a chat that is worth a system notification, from the status before and after it.
 * One kind per transition, so a status that did not move rings never. A reply the user stopped, or a silent turn that left no reply, is not news to them.
 */
export const chatNotice = (prev: SessionStatus, next: SessionStatus, ev: ChatEvent): ChatNoticeKind | null => {
  if (next === 'needs-approval' && prev !== 'needs-approval') return 'approval'
  if (next === 'error' && prev !== 'error') return 'failed'
  if (next === 'done' && ev.event === 'done' && !ev.data.segment && !ev.data.stopped && ev.data.id) return 'reply'  // no id: a silent wake, its reply row is gone
  return null
}

/** A `run_state` frame as the finish rule reads it: `replied` is true once the run published a visible reply (not a silent wake), `stopped` once the user hit Stop. */
export type FinishInfo = RunInfo

/**
 * The one finish rule for a run this window did not stream, from the frame before it and the frame now.
 * Fires on the transition only: the reply becoming whole (`replied`, which a silent wake never sets), or the run
 * ending with an error. Never for a job or worker run, a stopped run, or a frame that repeats what was already seen;
 * the caller's per-run key stops the same run ringing twice across the stream and this feed.
 */
export const finishNotice = (prev: FinishInfo | undefined, info: FinishInfo): 'reply' | 'failed' | null => {
  if ((info.kind !== 'chat' && info.kind !== 'desk') || info.stopped) return null
  if (prev && (prev.replied || !prev.live)) return null
  if (info.error || info.status === 'error') return info.replied || !info.live ? 'failed' : null
  return info.replied ? 'reply' : null
}

/** The sidebar pulse: a session's own status wins, and a conversation with no session falls back to its live run. */
export const pulseStatus = (sessionStatus: SessionStatus, liveRun?: { status?: string } | null): SessionStatus => {
  if (sessionStatus !== 'idle') return sessionStatus
  if (!liveRun) return 'idle'
  return liveRun.status === 'awaiting_approval' ? 'needs-approval' : 'working'
}
