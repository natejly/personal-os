/** Window-level backstop: a rejection nobody caught becomes one toast instead of a silent no-op. */

/** The user-facing text for an unhandled rejection, or null when it is not worth a toast (an abort, a dropped fetch). */
export function describeRejection(e: unknown): string | null {
  const name = (e as { name?: string } | null)?.name
  if (name === 'AbortError') return null
  const msg = e instanceof Error ? e.message : typeof e === 'string' ? e : ''
  // A fetch that never reached the backend: the restart banner already says so.
  if (e instanceof TypeError && /failed to fetch|load failed|networkerror/i.test(msg)) return null
  const text = msg.trim()
  return text ? text.slice(0, 300) : null
}

const DEDUPE_MS = 10_000

/**
 * The toast text for a rejection, or null when it is not worth one or the same text was surfaced
 * within the last few seconds. `seen` is the caller's text → time map; entries past the window are
 * dropped on the way through so it cannot grow with every distinct message.
 */
export function rejectionToast(reason: unknown, now: number, seen: Map<string, number>): string | null {
  for (const [text, at] of seen) if (now - at >= DEDUPE_MS) seen.delete(text)
  const text = describeRejection(reason)
  if (!text || seen.has(text)) return null
  seen.set(text, now)
  return text
}

let installed = false
const recent = new Map<string, number>()

/**
 * One listener per window. The default console report is left alone (the main process copies it into
 * its log); the toast is added on top, once per distinct message within a few seconds.
 */
export function installRejectionToasts(toast: (text: string, kind: 'error') => void): void {
  if (installed || typeof window === 'undefined' || typeof window.addEventListener !== 'function') return
  installed = true
  window.addEventListener('unhandledrejection', (ev: PromiseRejectionEvent) => {
    const text = rejectionToast(ev.reason, Date.now(), recent)
    if (text) toast(text, 'error')
  })
}
