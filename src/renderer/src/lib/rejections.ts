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

let installed = false
const recent = new Map<string, number>()
const DEDUPE_MS = 10_000

/**
 * One listener per window. The default console report is left alone (the main process copies it into
 * its log); the toast is added on top, once per distinct message within a few seconds.
 */
export function installRejectionToasts(toast: (text: string, kind: 'error') => void): void {
  if (installed || typeof window === 'undefined' || typeof window.addEventListener !== 'function') return
  installed = true
  window.addEventListener('unhandledrejection', (ev: PromiseRejectionEvent) => {
    const text = describeRejection(ev.reason)
    if (!text) return
    const now = Date.now()
    if (now - (recent.get(text) ?? -Infinity) < DEDUPE_MS) return
    recent.set(text, now)
    toast(text, 'error')
  })
}
