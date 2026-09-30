type Key = 'board' | 'dashboard'

/**
 * One-shot selection passed to a classic view whose selection is component state (BoardsView,
 * DashboardsView), so a canvas window's Expand can land on the right board or dashboard. The view
 * peeks it in its `useState` initialiser and clears it in a mount effect, which is StrictMode-safe:
 * a double-invoked initialiser still sees the value.
 */
const pending = new Map<Key, string>()

export const handoff = (key: Key, id: string | null): void => {
  if (id) pending.set(key, id)
  else pending.delete(key)
}
export const peekHandoff = (key: Key): string | null => pending.get(key) ?? null
export const clearHandoff = (key: Key): void => {
  pending.delete(key)
}
