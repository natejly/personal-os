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

/** One line under a worker's title: its position while queued, the current action while running. */
export function workerLine(w: Pick<WorkerInfo, 'status' | 'now' | 'queue_position'>): string {
  if (w.status === 'queued') return w.queue_position ? `Queued, number ${w.queue_position}` : 'Queued'
  if (w.status === 'running' && w.now) return w.now
  return LABEL[w.status]
}

/** Live workers first (oldest first within them, so the order does not jump), then ended ones newest first. */
export function sortWorkers(ws: WorkerInfo[]): WorkerInfo[] {
  return [...ws].sort((a, b) => Number(workerIsLive(b)) - Number(workerIsLive(a)) || (workerIsLive(a) ? a.started_at - b.started_at : b.started_at - a.started_at))
}

/** Replace or add one worker (an event's payload) in the list. */
export const upsertWorker = (ws: WorkerInfo[], w: WorkerInfo): WorkerInfo[] => (ws.some((x) => x.id === w.id) ? ws.map((x) => (x.id === w.id ? w : x)) : [w, ...ws])

/** Wake turns tell the assistant a worker ended; they are not the user's words and never show. */
export const isWake = (m: Pick<Message, 'kind'>): boolean => m.kind === 'wake'

/** The same array when nothing is hidden, so memoised callers keep their identity. */
export const withoutWake = <T extends Pick<Message, 'kind'>>(messages: T[] | undefined): T[] | undefined =>
  messages?.some(isWake) ? messages.filter((m) => !isWake(m)) : messages
