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
  queued: 'Queued', running: 'Working', awaiting_approval: 'Needs approval', done: 'Done', error: 'Failed', interrupted: 'Interrupted', stopped: 'Stopped'
}

/** The short word beside a worker's title: its place in line while queued, else its status. */
export const workerWord = (w: Pick<WorkerInfo, 'status' | 'queue_position'>): string =>
  w.status === 'queued' && w.queue_position ? `Queued #${w.queue_position}` : LABEL[w.status]

/** Live workers first (oldest first within them, so the order does not jump), then ended ones newest first. */
export function sortWorkers(ws: WorkerInfo[]): WorkerInfo[] {
  return [...ws].sort((a, b) => Number(workerIsLive(b)) - Number(workerIsLive(a)) || (workerIsLive(a) ? a.started_at - b.started_at : b.started_at - a.started_at))
}

/** Replace or add one worker (an event's payload) in the list. */
export const upsertWorker = (ws: WorkerInfo[], w: WorkerInfo): WorkerInfo[] => (ws.some((x) => x.id === w.id) ? ws.map((x) => (x.id === w.id ? w : x)) : [w, ...ws])

/** Any non-null kind marks a control message for the model (wake, nudge, continue, ...); it is not the user's words and never shows. */
export const isInternal = (m: Pick<Message, 'kind'>): boolean => m.kind != null

/** The same array when nothing is hidden, so memoised callers keep their identity. */
export const withoutInternal = <T extends Pick<Message, 'kind'>>(messages: T[] | undefined): T[] | undefined =>
  messages?.some(isInternal) ? messages.filter((m) => !isInternal(m)) : messages
