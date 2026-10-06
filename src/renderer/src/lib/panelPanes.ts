import type { ShowItem } from '@shared/types'

/**
 * The side panel's state for one chat: the last few things shown (never persisted) and which of them the one or
 * two panes display. Pure, so node:test pins open / split / pick / close and the history cap.
 */

export const HISTORY_CAP = 8
export type Pane = 'left' | 'right'
export interface Entry { id: number; item: ShowItem; at: number }
export interface PanelState {
  entries: Entry[]
  nextId: number
  left: number
  /** Set while the panel is split. */
  right: number | null
  /** The pane highlighted when split. */
  active: Pane
}

/** What identifies an item: the same file or the same source is one entry, not a second row in the picker. */
export const itemKey = (i: ShowItem): string => `${i.kind}:${i.kind === 'file' ? i.path : i.source}`

export function entryOf(s: PanelState, pane: Pane): Entry | undefined {
  const id = pane === 'left' ? s.left : s.right
  return s.entries.find((e) => e.id === id)
}

/** Show `item`. Single: replaces the left pane. Split: lands in `pane`, else the right. `pane: 'right'` splits. */
export function open(s: PanelState | undefined, item: ShowItem, now: number, pane?: Pane): PanelState {
  const base: PanelState = s ?? { entries: [], nextId: 1, left: 0, right: null, active: 'left' }
  const key = itemKey(item)
  const hit = base.entries.find((e) => itemKey(e.item) === key)
  const id = hit ? hit.id : base.nextId
  const entry: Entry = { id, item, at: now }
  let entries = hit ? base.entries.map((e) => (e.id === id ? entry : e)) : [...base.entries, entry]
  // A brand-new panel has nothing on the left, so a first show "to the right" just fills the left.
  const target: Pane = !s ? 'left' : (pane ?? (base.right !== null ? 'right' : 'left'))
  const next: PanelState = {
    entries,
    nextId: hit ? base.nextId : base.nextId + 1,
    left: target === 'left' ? id : base.left,
    right: target === 'right' ? id : base.right,
    active: target
  }
  // Evict the oldest entry no pane shows.
  while (entries.length > HISTORY_CAP) {
    const drop = entries.find((e) => e.id !== next.left && e.id !== next.right)
    if (!drop) break
    entries = entries.filter((e) => e !== drop)
  }
  return { ...next, entries }
}

/** Split into two panes; the new right pane starts on the most recent other entry (or the same one). */
export function split(s: PanelState): PanelState {
  if (s.right !== null) return s
  const other = [...s.entries].reverse().find((e) => e.id !== s.left)
  return { ...s, right: other ? other.id : s.left, active: 'right' }
}

export function pick(s: PanelState, pane: Pane, id: number): PanelState {
  if (!s.entries.some((e) => e.id === id) || (pane === 'right' && s.right === null)) return s
  return { ...s, [pane]: id, active: pane }
}

/** Close one pane of a split (the other stays, as the only pane); closing the only pane returns null. */
export function closePane(s: PanelState, pane: Pane): PanelState | null {
  if (s.right === null) return null
  return { ...s, left: pane === 'left' ? s.right : s.left, right: null, active: 'left' }
}
