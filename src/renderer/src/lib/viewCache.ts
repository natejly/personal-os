/**
 * The last payload a Google screen painted, kept in this window.
 *
 * Opening Calendar or Mail used to wait on a full refetch before anything was
 * drawn. The saved copy is shown immediately; the request that follows only
 * has to fold in what changed.
 */

const PREFIX = 'grain.gview.'
const mem = new Map<string, unknown>()

export function readView<T>(key: string): T | null {
  if (mem.has(key)) return mem.get(key) as T
  try {
    const raw = localStorage.getItem(PREFIX + key)
    if (!raw) return null
    const parsed = JSON.parse(raw) as T
    mem.set(key, parsed)
    return parsed
  } catch {
    return null
  }
}

export function writeView<T>(key: string, value: T): void {
  mem.set(key, value)
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value))
  } catch { /* a full disk or a private window still shows this session's copy */ }
}

/** Drop every saved Google screen. Used when the account changes, so one inbox cannot flash as another. */
export function clearViews(): void {
  mem.clear()
  try {
    const drop: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (k && k.startsWith(PREFIX)) drop.push(k)
    }
    for (const k of drop) localStorage.removeItem(k)
  } catch { /* nothing stored */ }
}

export const calendarViewKey = (startIso: string, days: number, query: string): string =>
  `cal:${startIso}:${days}:${query}`
