import type { ChatEvent } from '@shared/types'

type TextEvent = Extract<ChatEvent, { event: 'delta' }>

export interface DeltaBufferOptions {
  intervalMs?: number
  now?: () => number
  setTimer?: (fn: () => void, ms: number) => unknown
  clearTimer?: (handle: unknown) => void
}

/**
 * Coalesces streamed text events so the store is written at most once per interval.
 *
 * The first event after a quiet spell applies immediately (slow streams and the first token behave as
 * before). Events inside the interval join one pending event of the same kind and id, and a single
 * timer applies it at the trailing edge. A different kind or id applies what is pending first, so
 * order is preserved. `flush()` applies anything pending and clears the timer.
 */
export function createDeltaBuffer(
  apply: (ev: ChatEvent) => void,
  opts: DeltaBufferOptions = {}
): { push: (ev: TextEvent) => void; flush: () => void } {
  const intervalMs = opts.intervalMs ?? 50
  const now = opts.now ?? Date.now
  const setTimer = opts.setTimer ?? ((fn, ms) => setTimeout(fn, ms))
  const clearTimer = opts.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>))
  let pending: TextEvent | null = null
  let timer: unknown = null
  let lastApplied = -Infinity

  const flush = (): void => {
    if (timer !== null) {
      clearTimer(timer)
      timer = null
    }
    if (!pending) return
    const ev = pending
    pending = null
    lastApplied = now()
    apply(ev)
  }

  const push = (ev: TextEvent): void => {
    if (pending && (pending.event !== ev.event || pending.data.id !== ev.data.id)) flush()
    if (pending) {
      pending = { event: pending.event, data: { id: pending.data.id, text: pending.data.text + ev.data.text } } as TextEvent
      return
    }
    if (timer === null && now() - lastApplied >= intervalMs) {
      lastApplied = now()
      apply(ev)
      return
    }
    pending = { event: ev.event, data: { id: ev.data.id, text: ev.data.text } } as TextEvent
    if (timer === null) timer = setTimer(() => { timer = null; flush() }, Math.max(0, intervalMs - (now() - lastApplied)))
  }

  return { push, flush }
}
