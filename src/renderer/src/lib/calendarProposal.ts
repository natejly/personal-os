/**
 * Pure layout logic for the calendar approval card's grid (toolcards/CalendarCard.tsx): which hours
 * to draw, how each block should look (new / moved / edited / deleted ...), where moved events came
 * from, and the spoken labels. No React, so it is unit tested (calendarProposal.test.ts).
 */
import type { Block, Change, Outcome, Placed } from './calendarOverlay'
import { layoutLanes } from './calendarOverlay'

export type Variant = 'existing' | 'new' | 'moved-from' | 'moved-to' | 'edited' | 'delete' | 'done' | 'failed'

export interface ViewBlock extends Placed {
  variant: Variant
  /** The change is switched off: drawn greyed out. */
  off: boolean
  /** On a moved-from block: where the event goes. */
  movedTo?: { day: string; startMin: number; endMin: number }
  /** On a moved-to block: where the event was. */
  movedFrom?: { day: string; startMin: number; endMin: number }
}

/** Gives every block its look. An update whose old slot is not drawn (same time) is "edited". */
export function classifyBlocks(blocks: Block[], enabled?: boolean[]): ViewBlock[] {
  const froms = new Map<number, Block>()
  const tos = new Map<number, Block>()
  for (const b of blocks) {
    if (b.change === undefined) continue
    if (b.kind === 'move-from') froms.set(b.change, b)
    if (b.kind === 'move-to') tos.set(b.change, b)
  }
  const variantOf = (b: Block): Variant => {
    switch (b.kind) {
      case 'create': return 'new'
      case 'move-from': return 'moved-from'
      case 'move-to': return b.change !== undefined && froms.has(b.change) ? 'moved-to' : 'edited'
      default: return b.kind
    }
  }
  const lanes = new Map(layoutLanes(blocks).map((p) => [p.key, p]))
  const out: ViewBlock[] = []
  for (const b of blocks) {
    const p = lanes.get(b.key) ?? { ...b, lane: 0, lanes: 1 }
    const v: ViewBlock = { ...p, variant: variantOf(b), off: b.change !== undefined && enabled?.[b.change] === false }
    const to = b.change !== undefined ? tos.get(b.change) : undefined
    const from = b.change !== undefined ? froms.get(b.change) : undefined
    if (v.variant === 'moved-from' && to) v.movedTo = { day: to.day, startMin: to.startMin, endMin: to.endMin }
    if (v.variant === 'moved-to' && from) v.movedFrom = { day: from.day, startMin: from.startMin, endMin: from.endMin }
    out.push(v)
  }
  return out
}

/**
 * Whole hours to draw: every timed block (existing, proposed, old slots, deletes) padded by `pad`
 * hours, widened to at least `minHours`, kept inside 0-24. With nothing timed, a working day window.
 */
export function hourRange(blocks: Pick<Block, 'startMin' | 'endMin' | 'allDay'>[], minHours = 4, pad = 1): { start: number; end: number } {
  const timed = blocks.filter((b) => !b.allDay)
  if (!timed.length) return { start: 9, end: 9 + Math.max(minHours, 8) }
  let start = Math.max(0, Math.floor(Math.min(...timed.map((b) => b.startMin)) / 60) - pad)
  let end = Math.min(24, Math.ceil(Math.max(...timed.map((b) => b.endMin)) / 60) + pad)
  // Widen alternately after the end and before the start until the window is tall enough.
  for (let n = 0; end - start < minHours && (start > 0 || end < 24); n++) {
    const growEnd = n % 2 === 0 ? end < 24 : start === 0
    if (growEnd) end += 1
    else start -= 1
  }
  return { start, end }
}

/** The earliest minute-of-day among the changed blocks (the grid scrolls here); null when none. */
export function firstChangeMin(blocks: Block[]): number | null {
  const mins = blocks.filter((b) => b.change !== undefined && !b.allDay).map((b) => b.startMin)
  return mins.length ? Math.min(...mins) : null
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const parts = (day: string): { y: number; m: number; d: number } => ({ y: +day.slice(0, 4), m: +day.slice(5, 7) - 1, d: +day.slice(8, 10) })
export const weekdayName = (day: string): string => { const p = parts(day); return WEEKDAYS[new Date(p.y, p.m, p.d).getDay()] }

/** "Oct 2", "Oct 2 – 3", "Sep 30 – Oct 2" from sorted YYYY-MM-DD keys. */
export function weekLabel(days: string[]): string {
  if (!days.length) return ''
  const a = parts(days[0]), b = parts(days[days.length - 1])
  if (days.length === 1 || (a.m === b.m && a.d === b.d && a.y === b.y)) return `${MONTHS[a.m]} ${a.d}`
  if (a.m === b.m && a.y === b.y) return `${MONTHS[a.m]} ${a.d} – ${b.d}`
  return `${MONTHS[a.m]} ${a.d} – ${MONTHS[b.m]} ${b.d}`
}

/** "2:00 PM". */
export function clock12(min: number): string {
  const m = ((min % 1440) + 1440) % 1440
  const h = Math.floor(m / 60)
  return `${h % 12 || 12}:${String(m % 60).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`
}

/** "2:00 to 3:00 PM", or "11:30 AM to 12:30 PM" across noon. */
export function rangeText(startMin: number, endMin: number): string {
  const a = clock12(startMin), b = clock12(endMin === 1440 ? 1439 : endMin)
  const end = endMin === 1440 ? '12:00 AM' : b
  return a.slice(-2) === end.slice(-2) ? `${a.slice(0, -3)} to ${end}` : `${a} to ${end}`
}

export const VARIANT_LABEL: Record<Variant, string> = {
  existing: 'Your event', new: 'New event', 'moved-from': 'Current time of moved event', 'moved-to': 'Moved event',
  edited: 'Edited event', delete: 'Event to delete', done: 'Done', failed: 'Not applied'
}

/** "New event Q4 planning with Ana, Friday 2:00 to 3:00 PM". */
export function blockAriaLabel(b: Pick<ViewBlock, 'variant' | 'summary' | 'day' | 'startMin' | 'endMin' | 'allDay' | 'off' | 'conflict'>, guests: string[] = []): string {
  const who = guests.length ? ` with ${guests.join(', ')}` : ''
  const when = b.allDay ? `${weekdayName(b.day)} all day` : `${weekdayName(b.day)} ${rangeText(b.startMin, b.endMin)}`
  return `${VARIANT_LABEL[b.variant]} ${b.summary}${who}, ${when}${b.conflict ? ', conflicts with another event' : ''}${b.off ? ', not included' : ''}`
}

export type LegendKey = 'new' | 'moved' | 'edited' | 'delete' | 'existing' | 'done' | 'failed'
const LEGEND_ORDER: LegendKey[] = ['new', 'moved', 'edited', 'delete', 'existing', 'done', 'failed']
export const LEGEND_LABEL: Record<LegendKey, string> = { new: 'New', moved: 'Moved', edited: 'Edited', delete: 'Deleted', existing: 'Your events', done: 'Done', failed: 'Not made' }

/** Legend chips for the variants actually on the grid, in a fixed order. */
export function legendKeys(blocks: Pick<ViewBlock, 'variant'>[]): LegendKey[] {
  const set = new Set<LegendKey>(blocks.map((b) => (b.variant === 'moved-from' || b.variant === 'moved-to' ? 'moved' : b.variant)))
  return LEGEND_ORDER.filter((k) => set.has(k))
}

/** Outcome of change `i`, for badging a block in the finished state. */
export const outcomeFor = (outcomes: Outcome[], i?: number): Outcome | undefined => (i === undefined ? undefined : outcomes.find((o) => o.i === i))

/** The change an index points at, tolerating drafts shorter than the blocks. */
export const changeAt = (changes: Change[], i?: number): Change | undefined => (i === undefined ? undefined : changes[i])
