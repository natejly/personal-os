import type { Message, WorkerInfo, WorkerStatus } from '@shared/types'

/** A worker that still holds (or waits for) a slot: the panel polls while any is. */
export const workerIsLive = (w: Pick<WorkerInfo, 'status'>): boolean => w.status === 'queued' || w.status === 'running' || w.status === 'awaiting_approval'

/** How many of a chat's workers are live. */
export const liveWorkerCount = (ws: readonly Pick<WorkerInfo, 'status'>[]): number => ws.filter(workerIsLive).length

/** The buttons a worker row offers. Resume needs a stored transcript; a live worker can only be stopped. */
export function workerActions(w: Pick<WorkerInfo, 'status' | 'resumable'>): { stop: boolean; resume: boolean } {
  return { stop: workerIsLive(w), resume: !workerIsLive(w) && (w.status === 'interrupted' || w.resumable) }
}

const LABEL: Record<WorkerStatus, string> = {
  queued: 'Queued', running: 'Working', awaiting_approval: 'Waiting for your approval', done: 'Done', error: 'Failed', interrupted: 'Interrupted', stopped: 'Stopped'
}

/** The short word beside a worker's title: its place in line while queued, else its status. */
export const workerWord = (w: Pick<WorkerInfo, 'status' | 'queue_position'>): string =>
  w.status === 'queued' && w.queue_position ? `Queued #${w.queue_position}` : LABEL[w.status]

/** Live workers first (oldest first within them, so the order does not jump), then ended ones newest first. */
export function sortWorkers(ws: WorkerInfo[]): WorkerInfo[] {
  return [...ws].sort((a, b) => Number(workerIsLive(b)) - Number(workerIsLive(a)) || (workerIsLive(a) ? a.started_at - b.started_at : b.started_at - a.started_at))
}

/** Ended workers listed on request; only the newest few, to keep the card short. */
export const ENDED_SHOWN = 4

/** The rows the workers card lists: every live worker, then (only when asked) the newest ended ones, in sort order. */
export function listedWorkers(ws: readonly WorkerInfo[], showFinished: boolean): WorkerInfo[] {
  const sorted = sortWorkers([...ws])
  return showFinished ? [...sorted.filter(workerIsLive), ...sorted.filter((x) => !workerIsLive(x)).slice(0, ENDED_SHOWN)] : sorted.filter(workerIsLive)
}

/** Replace or add one worker (an event's payload) in the list. */
export const upsertWorker = (ws: WorkerInfo[], w: WorkerInfo): WorkerInfo[] => (ws.some((x) => x.id === w.id) ? ws.map((x) => (x.id === w.id ? w : x)) : [w, ...ws])

/** Any non-null kind marks a control message for the model (wake, nudge, continue, ...); it is not the user's words and never shows. Messages from another chat ('chat_in', 'chat_reply') and the `/clear` marker are the exception: they render. */
export const isInternal = (m: Pick<Message, 'kind'>): boolean => m.kind != null && m.kind !== 'chat_in' && m.kind !== 'chat_reply' && m.kind !== 'clear' && m.kind !== 'branch_summary'

/** The note a fork carries about the branch it left; it shows collapsed. */
export const isBranchNote = (m: Pick<Message, 'kind'>): boolean => m.kind === 'branch_summary'

/** The `/clear` marker row: the model's context starts after it; the transcript shows a divider. */
export const isClear = (m: Pick<Message, 'kind'>): boolean => m.kind === 'clear'

/** What a chat shows after `/clear`: from the latest marker on. `hidden` counts the rows above it; the same array when there is no marker. */
export const sinceClear = <T extends Pick<Message, 'kind'>>(messages: T[]): { shown: T[]; hidden: number } => {
  const i = messages.findLastIndex(isClear)
  return i <= 0 ? { shown: messages, hidden: 0 } : { shown: messages.slice(i), hidden: i }
}

/** The same array when nothing is hidden, so memoised callers keep their identity. */
export const withoutInternal = <T extends Pick<Message, 'kind'>>(messages: T[] | undefined): T[] | undefined =>
  messages?.some(isInternal) ? messages.filter((m) => !isInternal(m)) : messages
