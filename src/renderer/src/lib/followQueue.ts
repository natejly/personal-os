/**
 * Follow-ups typed while a reply is still running. Enter queues them; each runs as its own turn,
 * one per finished run, in order. ⌘Enter still steers the live reply. Stop or a failure pauses the
 * queue, so nothing is sent on top of a run that went wrong until the user resumes.
 *
 * The queue lives in the drafts store under `q:<conversationId>` as JSON, which gives it the drafts'
 * persistence (survives a reload, shared with other windows through the `storage` event) for free.
 * Everything above the storage helpers is pure, for the tests.
 */
import type { Attachment } from '@shared/types'
import { getDraft, refreshDraft, setDraftNow } from './drafts'

export interface QueuedItem { id: string; text: string; files?: Attachment[] }
export interface FollowQueue { items: QueuedItem[]; paused: boolean }

export const EMPTY_QUEUE: FollowQueue = { items: [], paused: false }

export const queueKey = (conversationId: string): string => `q:${conversationId}`

export function parseQueue(raw: string | undefined): FollowQueue {
  if (!raw) return EMPTY_QUEUE
  try {
    const p = JSON.parse(raw) as { items?: unknown; paused?: unknown }
    const items = Array.isArray(p.items)
      ? p.items.filter((i): i is QueuedItem => !!i && typeof i.id === 'string' && typeof i.text === 'string' && !!i.text)
      : []
    return { items, paused: p.paused === true && items.length > 0 }
  } catch {
    return EMPTY_QUEUE
  }
}

/** An empty queue is stored as nothing, so the drafts store drops the key. */
export const serializeQueue = (q: FollowQueue): string => (q.items.length ? JSON.stringify(q) : '')

export function enqueue(q: FollowQueue, text: string, id: string, files?: Attachment[]): FollowQueue {
  if (!text.trim() && !files?.length) return q
  return { ...q, items: [...q.items, files?.length ? { id, text, files } : { id, text }] }
}

export function removeQueued(q: FollowQueue, id: string): FollowQueue {
  const items = q.items.filter((i) => i.id !== id)
  return { items, paused: q.paused && items.length > 0 }
}

export interface DoneInfo { segment?: boolean; error?: string | null; stopped?: boolean }

/**
 * What a run's `done` does to the queue. A steer segment's done is not the end of the run, so it
 * leaves the queue alone; a stopped or failed run pauses it; a clean final done hands out exactly
 * one item, and the next waits for the next run's done.
 */
export function nextOnDone(q: FollowQueue, done: DoneInfo): { queue: FollowQueue; send: QueuedItem | null } {
  if (done.segment || !q.items.length) return { queue: q, send: null }
  if (done.error || done.stopped) return { queue: pauseQueue(q), send: null }
  if (q.paused) return { queue: q, send: null }
  const [send, ...items] = q.items
  return { queue: { items, paused: false }, send }
}

export const pauseQueue = (q: FollowQueue): FollowQueue => (q.items.length ? { ...q, paused: true } : q)

/** A queued send that was refused goes back in front, and the queue waits for the user. */
export const requeueFront = (q: FollowQueue, item: QueuedItem): FollowQueue => ({ items: [item, ...q.items.filter((i) => i.id !== item.id)], paused: true })

export type EnterAction = 'send' | 'queue' | 'steer' | 'confirm-steer'

/**
 * What Enter does in a chat composer. Idle: send. While a reply runs: Enter queues and ⌘Enter steers.
 * A steer while an approval or plan card is open declines that card (the typed text is the reason the
 * assistant gets), so ⌘Enter asks first there. A desk's run never declines a card on a steer.
 */
export function enterAction(args: { busy: boolean; mod: boolean; cardPending: boolean; desk?: boolean }): EnterAction {
  if (!args.busy) return 'send'
  if (!args.mod) return 'queue'
  return args.cardPending && !args.desk ? 'confirm-steer' : 'steer'
}

// ---- storage ----

export function readQueue(conversationId: string): FollowQueue {
  return parseQueue(getDraft(queueKey(conversationId))?.text)
}

/** Written through at once, not debounced: another window deciding whether to dequeue reads storage. */
export function writeQueue(conversationId: string, q: FollowQueue): void {
  setDraftNow(queueKey(conversationId), serializeQueue(q))
}

export function updateQueue(conversationId: string, fn: (q: FollowQueue) => FollowQueue): void {
  const q = readQueue(conversationId)
  const next = fn(q)
  if (next !== q) writeQueue(conversationId, next)
}

/**
 * Takes the next item for a run that just finished, re-reading storage first so two windows watching
 * the same run do not both send it.
 * ponytail: read-then-write on localStorage is not atomic across windows; a lock if that race ever shows.
 */
export function claimNext(conversationId: string, done: DoneInfo): QueuedItem | null {
  refreshDraft(queueKey(conversationId))
  const before = readQueue(conversationId)
  const { queue, send } = nextOnDone(before, done)
  if (queue !== before) writeQueue(conversationId, queue)
  return send
}

/** Claims the next item and sends it as its own turn; a refused send goes back in front and pauses the queue. */
export function sendNext(conversationId: string, done: DoneInfo, send: (text: string, files?: Attachment[]) => Promise<boolean>): void {
  const item = claimNext(conversationId, done)
  if (!item) return
  void send(item.text, item.files).catch(() => false).then((ok) => {
    if (!ok) updateQueue(conversationId, (q) => requeueFront(q, item))
  })
}
