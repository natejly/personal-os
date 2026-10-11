import { create } from 'zustand'

/**
 * Sidebar chat multi-select. `order` is the chat ids as the rows are laid out, so a shift-click can
 * take everything between the anchor (the last chat clicked) and the one clicked now.
 * ⌘-click toggles one chat; either way the clicked chat becomes the anchor.
 */
export function nextSelection(rows: string[], selected: string[], anchor: string | null, id: string, range: boolean): string[] {
  const order = [...new Set(rows)] // a chat can render in two sections
  const a = anchor ? order.indexOf(anchor) : -1
  const b = order.indexOf(id)
  if (range && a >= 0 && b >= 0) return order.slice(Math.min(a, b), Math.max(a, b) + 1)
  return selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]
}

interface SelectionState {
  selected: string[]
  anchor: string | null
  /** A modified click on a row; `order` is read from the DOM by the caller. `fallback` anchors a first shift-click (the open chat). */
  pick: (order: string[], id: string, range: boolean, fallback?: string | null) => void
  /** A plain click: nothing selected, but this chat is where a shift-click range starts. */
  anchorAt: (id: string) => void
  reset: () => void
}

export const useChatSelection = create<SelectionState>((set, get) => ({
  selected: [],
  anchor: null,
  pick: (order, id, range, fallback) => set({ selected: nextSelection(order, get().selected, get().anchor ?? fallback ?? null, id, range), anchor: id }),
  anchorAt: (id) => set({ selected: [], anchor: id }),
  reset: () => set({ selected: [], anchor: null })
}))
