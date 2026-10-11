import { create } from 'zustand'

/**
 * Sidebar chat multi-select. `order` is the chat ids as the rows are laid out, so a shift-click can
 * take everything between the anchor (the last chat clicked) and the one clicked now.
 * ⌘-click toggles one chat; either way the clicked chat becomes the anchor.
 */
export function nextSelection(order: string[], selected: string[], anchor: string | null, id: string, range: boolean): string[] {
  const a = anchor ? order.indexOf(anchor) : -1
  const b = order.indexOf(id)
  if (range && a >= 0 && b >= 0) return order.slice(Math.min(a, b), Math.max(a, b) + 1)
  return selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]
}

interface SelectionState {
  selected: string[]
  anchor: string | null
  /** A modified click on a row; `order` is read from the DOM by the caller. */
  pick: (order: string[], id: string, range: boolean) => void
  reset: () => void
}

export const useChatSelection = create<SelectionState>((set, get) => ({
  selected: [],
  anchor: null,
  pick: (order, id, range) => set({ selected: nextSelection(order, get().selected, get().anchor, id, range), anchor: id }),
  reset: () => set({ selected: [], anchor: null })
}))
