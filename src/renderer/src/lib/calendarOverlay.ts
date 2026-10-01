/**
 * Pure logic behind the calendar tool cards: reading a tool call's arguments and result into
 * changes and outcomes, laying proposed changes over the user's real events, and the small
 * conflict/agenda helpers. No React, no network, so the awkward cases are unit tested
 * (calendarOverlay.test.ts).
 *
 * Everything a card shows must be derivable from the persisted run event (arguments +
 * result_preview), never from component state alone, so a card reads the same after a reload.
 */
import type { CalendarEvent } from '@shared/types'
import { dayKey } from '../components/CalendarWeek'

export type Op = 'create' | 'update' | 'delete'

/** One change in a calendar_propose batch (or the single call behind calendar_create/update/delete). */
export interface Change {
  op: Op
  event_id?: string
  calendar_id?: string
  summary?: string
  start?: string
  end?: string
  attendees?: string[]
  location?: string
  description?: string
  recurrence?: string[]
  conference?: boolean
  send_updates?: string
}

/** What happened to one change, as the backend recorded it (calendar_propose result rows). */
export interface Outcome {
  i: number
  op: Op
  ok: boolean
  /** Read-back verdict: verified | unverified | mismatch | unchecked. */
  v?: string
  err?: string
  id?: string
  cal?: string
  link?: string
  s?: string
  a?: string
  b?: string
}

export const CALENDAR_WRITE_TOOLS = ['calendar_propose', 'calendar_create', 'calendar_update', 'calendar_delete'] as const
const SINGLE_OP: Record<string, Op> = { calendar_create: 'create', calendar_update: 'update', calendar_delete: 'delete' }

const CHANGE_KEYS: (keyof Change)[] = ['op', 'event_id', 'calendar_id', 'summary', 'start', 'end', 'attendees', 'location', 'description', 'recurrence', 'conference', 'send_updates']

const str = (v: unknown): string | undefined => (typeof v === 'string' && v !== '' ? v : undefined)
const strs = (v: unknown): string[] | undefined => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : undefined)

function toChange(o: Record<string, unknown>, op: Op): Change {
  const c: Change = { op }
  for (const k of CHANGE_KEYS) {
    if (k === 'op') continue
    const v = o[k]
    if (v === undefined || v === null) continue
    if (k === 'attendees' || k === 'recurrence') { const a = strs(v); if (a) (c as unknown as Record<string, unknown>)[k] = a }
    else if (k === 'conference') { if (typeof v === 'boolean') c.conference = v }
    else { const s = str(v); if (s !== undefined) (c as unknown as Record<string, unknown>)[k] = s }
  }
  // calendar_create's own spelling of "add a Meet link".
  if (o.create_meet === true) c.conference = true
  return c
}

/** The changes a card should show for a call, whichever of the four tools made it. */
export function changesFromArgs(name: string, args: Record<string, unknown> | null | undefined): Change[] {
  const a = args ?? {}
  if (name === 'calendar_propose') {
    const raw = Array.isArray(a.changes) ? a.changes : []
    return raw.filter((c): c is Record<string, unknown> => !!c && typeof c === 'object')
      .map((c) => toChange(c, (['create', 'update', 'delete'] as const).find((o) => o === c.op) ?? 'create'))
  }
  const op = SINGLE_OP[name]
  return op ? [toChange(a, op)] : []
}

/**
 * The arguments to send back for an edited card. For calendar_propose that is the batch; for a
 * single-event tool it is the original flat arguments with the edited fields written over them,
 * so fields the card does not edit (reminders, color…) survive untouched.
 */
export function argsFromChanges(name: string, original: Record<string, unknown>, changes: Change[]): Record<string, unknown> {
  if (name === 'calendar_propose') return { ...original, changes: changes.map(clean) }
  const c = changes[0]
  if (!c) return original
  const out: Record<string, unknown> = { ...original }
  for (const k of ['summary', 'start', 'end', 'location', 'attendees', 'send_updates'] as const) {
    const v = c[k]
    if (v === undefined || (Array.isArray(v) && v.length === 0 && original[k] === undefined)) delete out[k]
    else out[k] = v
  }
  return out
}

function clean(c: Change): Change {
  const o: Record<string, unknown> = {}
  for (const k of CHANGE_KEYS) { const v = c[k]; if (v !== undefined) o[k] = v }
  return o as unknown as Change
}

/** Why one change cannot be approved as it stands, or null. Mirrors the server's own checks. */
export function changeProblem(c: Change): string | null {
  if (c.op === 'delete') return c.event_id ? null : 'Missing the event to delete'
  if (c.op === 'create' && !(c.summary ?? '').trim()) return 'Needs a title'
  if (c.op === 'create' && !c.start) return 'Needs a start'
  if (c.start) {
    const a = toDate(c.start)
    if (Number.isNaN(a.getTime())) return 'Start is not a date'
    if (c.end) {
      const b = toDate(c.end)
      if (Number.isNaN(b.getTime())) return 'End is not a date'
      if (isDay(c.start) !== isDay(c.end)) return 'Start and end must both be dates or both be times'
      if (b < a) return 'Ends before it starts'
    }
  }
  if ((c.attendees ?? []).some((x) => !/^[^\s@]+@[^\s@]+$/.test(x))) return 'Check the guest emails'
  return null
}

export const sameChanges = (a: Change[], b: Change[]): boolean => JSON.stringify(a.map(clean)) === JSON.stringify(b.map(clean))

// ------------------------------------------------------------------ results
export interface Verification { status: string }

export interface ParsedResult {
  /** The result JSON when it parsed; null when it was missing or cut mid-structure. */
  data: Record<string, unknown> | null
  /** Per-change outcomes, index-aligned with the changes when the backend returned them. */
  outcomes: Outcome[]
  /** The preview dropped rows (it is capped), so some changes have no outcome here. */
  truncated: boolean
}

export function parseResult(name: string, preview: string | null | undefined, error?: string | null): ParsedResult {
  let data: Record<string, unknown> | null = null
  if (preview) {
    try {
      const v = JSON.parse(preview)
      if (v && typeof v === 'object' && !Array.isArray(v)) data = v as Record<string, unknown>
    } catch { /* cut mid-structure: fall through with no data */ }
  }
  const out: ParsedResult = { data, outcomes: [], truncated: !!data?.truncated }
  if (!data) return out
  if (name === 'calendar_propose' && Array.isArray(data.results)) {
    out.outcomes = (data.results as Record<string, unknown>[]).map((r) => ({
      i: Number(r.i), op: (r.op as Op) ?? 'create', ok: r.ok === true, v: str(r.v), err: str(r.err), id: str(r.id), cal: str(r.cal),
      link: str(r.link), s: str(r.s), a: str(r.a), b: str(r.b)
    }))
    return out
  }
  if (SINGLE_OP[name]) {
    const ver = data.verification as Verification | undefined
    const failed = !!(error ?? data.error)
    out.outcomes = [{
      i: 0, op: SINGLE_OP[name], ok: !failed, v: ver?.status ?? (data.verified === true ? 'verified' : undefined),
      err: failed ? String(error ?? data.error) : undefined,
      id: str(data.id) ?? str(data.deleted), cal: str(data.calendar_id), link: str(data.link),
      s: str(data.summary), a: str(data.start), b: str(data.end)
    }]
  }
  return out
}

export type CardState = 'awaiting' | 'running' | 'denied' | 'failed' | 'partial' | 'done'

export interface StateInput {
  needs_approval?: boolean
  pending?: boolean
  approval?: string | null
  error?: string | null
  result_preview?: string
}

/** Where a call is in its life, from the event alone. */
export function cardState(name: string, e: StateInput, pendingProp = false): CardState {
  if (e.needs_approval ?? (pendingProp && !e.result_preview)) return 'awaiting'
  if (e.pending) return 'running'
  if (e.approval === 'deny' || /just declined|declined by the user/i.test(e.error ?? '')) return 'denied'
  const r = parseResult(name, e.result_preview, e.error)
  if (name === 'calendar_propose' && r.outcomes.length) {
    const ok = r.outcomes.filter((o) => o.ok).length
    if (ok === r.outcomes.length && !r.truncated) return 'done'
    return ok === 0 ? 'failed' : 'partial'
  }
  return e.error ? 'failed' : 'done'
}

export const notConnected = (error?: string | null): boolean => /google is not connected|not connected|sign in again|reconnect/i.test(error ?? '')

// ------------------------------------------------------------------ time
/** A local Date from the ISO forms the tools use: naive wall clock, offset, or a bare date. */
export function toDate(iso: string): Date {
  return iso.length === 10 ? new Date(`${iso}T00:00:00`) : new Date(iso)
}
const isDay = (s?: string): boolean => !!s && s.length === 10
const minOf = (d: Date): number => d.getHours() * 60 + d.getMinutes()
const pad = (n: number): string => String(n).padStart(2, '0')

/** `YYYY-MM-DDTHH:MM` in local time, the value a datetime-local input takes and gives. */
export const toInput = (iso: string): string => {
  if (isDay(iso)) return iso
  const d = toDate(iso)
  return Number.isNaN(d.getTime()) ? '' : `${dayKey(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// ------------------------------------------------------------------ overlay
export type BlockKind = 'existing' | 'create' | 'move-from' | 'move-to' | 'delete' | 'done' | 'failed'

export interface Block {
  key: string
  kind: BlockKind
  summary: string
  day: string
  startMin: number
  endMin: number
  allDay: boolean
  /** Index into the changes this block belongs to; absent for untouched events. */
  change?: number
  color?: string | null
  conflict?: boolean
}

/** A timed span clipped into one block per local day it touches (an overnight event gets two). */
function spans(startIso: string, endIso: string | undefined): { day: string; startMin: number; endMin: number; allDay: boolean }[] {
  if (isDay(startIso)) {
    // All-day: the end date is exclusive (and may be omitted for a single day).
    const first = toDate(startIso)
    let last = endIso && isDay(endIso) ? toDate(endIso) : new Date(first.getTime() + 86_400_000)
    if (last <= first) last = new Date(first.getTime() + 86_400_000)
    const out: { day: string; startMin: number; endMin: number; allDay: boolean }[] = []
    for (let d = new Date(first), n = 0; d < last && n < 14; d = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1), n++) {
      out.push({ day: dayKey(d), startMin: 0, endMin: 1440, allDay: true })
    }
    return out
  }
  const s = toDate(startIso)
  if (Number.isNaN(s.getTime())) return []
  let e = endIso ? toDate(endIso) : new Date(s.getTime() + 3_600_000)
  if (Number.isNaN(e.getTime()) || e <= s) e = new Date(s.getTime() + 3_600_000)
  const out: { day: string; startMin: number; endMin: number; allDay: boolean }[] = []
  for (let d = new Date(s.getFullYear(), s.getMonth(), s.getDate()), n = 0; d < e && n < 14; d = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1), n++) {
    const next = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1)
    const a = s > d ? s : d
    const b = e < next ? e : next
    out.push({ day: dayKey(d), startMin: minOf(a), endMin: b >= next ? 1440 : minOf(b), allDay: false })
  }
  return out
}

/** Where an update lands: its own start/end where given, else the event's old time (and duration). */
export function newPosition(c: Change, old?: Pick<CalendarEvent, 'start' | 'end'> | null): { start?: string; end?: string } {
  const start = c.start ?? old?.start
  if (!start) return {}
  if (c.end) return { start, end: c.end }
  if (c.start && old && !isDay(c.start) && !isDay(old.start)) {
    // Moved without a new end: keep the event's length.
    const len = toDate(old.end).getTime() - toDate(old.start).getTime()
    if (len > 0) return { start, end: new Date(toDate(start).getTime() + len).toISOString() }
  }
  return { start, end: c.start ? undefined : old?.end }
}

export interface OverlayOpts {
  /** Per-change on/off; missing means on. A switched-off change draws nothing. */
  enabled?: boolean[]
  /** Events by id for the update/delete targets, wherever they were found. */
  old?: Record<string, CalendarEvent>
  /** Present once the call has run: draws the final state instead of the plan. */
  outcomes?: Outcome[] | null
  colorOf?: (e: CalendarEvent) => string | null
}

const push = (out: Block[], b: Omit<Block, 'startMin' | 'endMin' | 'day' | 'allDay'>, startIso: string, endIso?: string): void => {
  spans(startIso, endIso).forEach((s, n) => out.push({ ...b, key: `${b.key}:${n}`, ...s }))
}

/** The user's events with the proposed (or finished) changes laid over them. */
export function buildOverlay(changes: Change[], existing: CalendarEvent[], opts: OverlayOpts = {}): Block[] {
  const { enabled, old = {}, outcomes = null, colorOf } = opts
  const on = (i: number): boolean => enabled?.[i] !== false
  const byId = (id?: string): CalendarEvent | undefined => (id ? (old[id] ?? existing.find((e) => e.id === id)) : undefined)
  const out: Block[] = []

  if (outcomes) {
    // Final state. The fetched events already contain whatever landed, so the touched ones are
    // redrawn from the outcome (highlighted) rather than twice.
    const touched = new Set(outcomes.map((o) => o.id).filter((x): x is string => !!x))
    for (const e of existing) {
      if (touched.has(e.id)) continue
      push(out, { key: `e:${e.id}`, kind: 'existing', summary: e.summary, color: colorOf?.(e) ?? null }, e.start, e.end)
    }
    changes.forEach((c, i) => {
      const o = outcomes.find((x) => x.i === i)
      if (!o) return
      if (c.op === 'delete') {
        if (!o.ok) { const e = byId(c.event_id); if (e) push(out, { key: `f:${i}`, kind: 'failed', summary: e.summary, change: i }, e.start, e.end) }
        return
      }
      const prior = byId(c.event_id)
      const pos = o.a ? { start: o.a, end: o.b } : c.op === 'update' ? newPosition(c, prior) : { start: c.start, end: c.end }
      if (!pos.start) return
      push(out, { key: `c:${i}`, kind: o.ok ? 'done' : 'failed', summary: o.s ?? c.summary ?? prior?.summary ?? 'Event', change: i }, pos.start, pos.end)
    })
    return out
  }

  const moved = new Set<string>()
  const removed = new Set<string>()
  changes.forEach((c, i) => {
    if (!on(i) || !c.event_id) return
    if (c.op === 'update') moved.add(c.event_id)
    if (c.op === 'delete') removed.add(c.event_id)
  })
  for (const e of existing) {
    if (removed.has(e.id)) continue // drawn below, struck through
    if (moved.has(e.id)) continue
    push(out, { key: `e:${e.id}`, kind: 'existing', summary: e.summary, color: colorOf?.(e) ?? null }, e.start, e.end)
  }
  changes.forEach((c, i) => {
    if (!on(i)) return
    if (c.op === 'create') {
      if (c.start) push(out, { key: `c:${i}`, kind: 'create', summary: c.summary || 'New event', change: i }, c.start, c.end)
      return
    }
    const prior = byId(c.event_id)
    if (c.op === 'delete') {
      if (prior) push(out, { key: `d:${i}`, kind: 'delete', summary: prior.summary, change: i }, prior.start, prior.end)
      return
    }
    const pos = newPosition(c, prior)
    const title = c.summary ?? prior?.summary ?? 'Event'
    if (prior && pos.start && spansDiffer(prior, pos)) push(out, { key: `f:${i}`, kind: 'move-from', summary: prior.summary, change: i }, prior.start, prior.end)
    if (pos.start) push(out, { key: `t:${i}`, kind: 'move-to', summary: title, change: i }, pos.start, pos.end)
    else if (prior) push(out, { key: `t:${i}`, kind: 'move-to', summary: title, change: i }, prior.start, prior.end)
  })
  return out
}

/** True when two events' instants really differ (offset-form and naive spellings of one time are equal). */
function spansDiffer(a: { start: string; end: string }, b: { start?: string; end?: string }): boolean {
  const same = (x?: string, y?: string): boolean => !x || !y || toDate(x).getTime() === toDate(y).getTime()
  return !(same(a.start, b.start) && same(a.end, b.end))
}

// ------------------------------------------------------------------ conflicts
/** For each switched-on timed create/update, the existing events (or earlier changes) it lands on. */
export function findConflicts(changes: Change[], existing: CalendarEvent[], opts: { enabled?: boolean[]; old?: Record<string, CalendarEvent> } = {}): Record<number, string[]> {
  const { enabled, old = {} } = opts
  const out: Record<number, string[]> = {}
  const leaving = new Set(changes.filter((c, i) => enabled?.[i] !== false && c.event_id && c.op !== 'create').map((c) => c.event_id as string))
  const busy = existing.filter((e) => !e.all_day && e.status !== 'cancelled' && e.transparency !== 'transparent' && !leaving.has(e.id))
  const placed: { i: number; a: number; b: number }[] = []
  changes.forEach((c, i) => {
    if (enabled?.[i] === false || c.op === 'delete') return
    const pos = c.op === 'update' ? newPosition(c, old[c.event_id ?? ''] ?? existing.find((e) => e.id === c.event_id)) : { start: c.start, end: c.end }
    if (!pos.start || isDay(pos.start)) return
    const a = toDate(pos.start).getTime()
    let b = pos.end ? toDate(pos.end).getTime() : a + 3_600_000
    if (!(b > a)) b = a + 3_600_000
    if (Number.isNaN(a)) return
    const hits: string[] = []
    for (const e of busy) {
      if (e.id === c.event_id) continue
      if (a < toDate(e.end).getTime() && toDate(e.start).getTime() < b) hits.push(e.summary || 'an event')
    }
    for (const p of placed) if (a < p.b && p.a < b) hits.push(`change ${p.i + 1}`)
    placed.push({ i, a, b })
    if (hits.length) out[i] = hits
  })
  return out
}

// ------------------------------------------------------------------ layout
export interface Placed extends Block { lane: number; lanes: number }

/** Side-by-side lanes for blocks that overlap within a day, like the week grid. */
export function layoutLanes(blocks: Block[]): Placed[] {
  const byDay = new Map<string, Block[]>()
  for (const b of blocks) if (!b.allDay) byDay.set(b.day, [...(byDay.get(b.day) ?? []), b])
  const out: Placed[] = []
  for (const list of byDay.values()) {
    const sorted = [...list].sort((x, y) => x.startMin - y.startMin || y.endMin - x.endMin)
    let cluster: Placed[] = []
    let clusterEnd = -1
    let laneEnds: number[] = []
    const flush = (): void => { for (const p of cluster) p.lanes = laneEnds.length; out.push(...cluster); cluster = []; laneEnds = [] }
    for (const b of sorted) {
      if (cluster.length && b.startMin >= clusterEnd) flush()
      let lane = laneEnds.findIndex((end) => end <= b.startMin)
      if (lane === -1) { lane = laneEnds.length; laneEnds.push(b.endMin) } else laneEnds[lane] = b.endMin
      cluster.push({ ...b, lane, lanes: 1 })
      clusterEnd = Math.max(clusterEnd, b.endMin)
    }
    flush()
  }
  return out
}

/** The whole-hour band worth drawing: every timed block, padded by an hour, never under `minHours`. */
export function hourBand(blocks: Block[], minHours = 5): { start: number; end: number } {
  const timed = blocks.filter((b) => !b.allDay)
  if (!timed.length) return { start: 8, end: 18 }
  let start = Math.max(0, Math.floor(Math.min(...timed.map((b) => b.startMin)) / 60) - 1)
  let end = Math.min(24, Math.ceil(Math.max(...timed.map((b) => b.endMin)) / 60) + 1)
  while (end - start < minHours && (start > 0 || end < 24)) {
    if (start > 0) start -= 1
    if (end - start < minHours && end < 24) end += 1
  }
  return { start, end }
}

export const MAX_DAYS = 5

/**
 * Which day columns to draw: the days the changes touch (their new and old positions), filled in
 * between when that span is short, else just the days with activity. Never more than MAX_DAYS.
 */
export function rangeDays(changes: Change[], old: Record<string, CalendarEvent> = {}, extra: string[] = []): string[] {
  const days = new Set<string>(extra)
  for (const c of changes) {
    const prior = c.event_id ? old[c.event_id] : undefined
    for (const p of [c.start && { start: c.start, end: c.end }, prior && { start: prior.start, end: prior.end }]) {
      if (p) for (const s of spans(p.start, p.end)) days.add(s.day)
    }
  }
  const sorted = [...days].sort()
  if (!sorted.length) return []
  const first = toDate(sorted[0]), last = toDate(sorted[sorted.length - 1])
  const span = Math.round((last.getTime() - first.getTime()) / 86_400_000) + 1
  if (span <= MAX_DAYS) {
    return Array.from({ length: span }, (_, n) => dayKey(new Date(first.getFullYear(), first.getMonth(), first.getDate() + n)))
  }
  return sorted.slice(0, MAX_DAYS)
}

// ------------------------------------------------------------------ agenda (find_time / events)
export interface AgendaItem { day: string; label: string; start: string; end: string; allDay: boolean; title?: string; link?: string | null; location?: string | null; slot?: boolean }

export function groupByDay<T extends { day: string }>(items: T[]): { day: string; items: T[] }[] {
  const m = new Map<string, T[]>()
  for (const it of items) m.set(it.day, [...(m.get(it.day) ?? []), it])
  return [...m.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([day, list]) => ({ day, items: list }))
}

export interface Slot { rank: number; start: string; end: string; minutes: number; label: string }

export function parseSlots(data: Record<string, unknown> | null): Slot[] {
  const raw = data && Array.isArray(data.slots) ? data.slots : []
  return raw.filter((s): s is Record<string, unknown> => !!s && typeof s === 'object' && typeof (s as Record<string, unknown>).start === 'string')
    .map((s, n) => ({ rank: Number(s.rank) || n + 1, start: String(s.start), end: String(s.end), minutes: Number(s.minutes) || 0, label: String(s.label ?? '') }))
}

export function parseAgendaEvents(data: Record<string, unknown> | null): CalendarEvent[] {
  const raw = data && Array.isArray(data.events) ? data.events : []
  return raw.filter((e): e is CalendarEvent => !!e && typeof e === 'object' && typeof (e as CalendarEvent).start === 'string')
}

const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const clock = (d: Date, meridiem: boolean): string => {
  const h = d.getHours() % 12 || 12
  return `${h}:${pad(d.getMinutes())}${meridiem ? (d.getHours() < 12 ? 'am' : 'pm') : ''}`
}

/** "Book 2:30–3:00pm Thu": the text a slot chip drops into the composer. */
export function slotReplyText(startIso: string, endIso: string): string {
  const s = toDate(startIso), e = toDate(endIso)
  const sameHalf = (s.getHours() < 12) === (e.getHours() < 12)
  return `Book ${clock(s, !sameHalf)}–${clock(e, true)} ${DOW[s.getDay()]}`
}

/** "Wed Oct 7": a day column or agenda heading, from a YYYY-MM-DD key. */
export function dayHeading(day: string): string {
  const d = toDate(day)
  return `${DOW[d.getDay()]} ${d.toLocaleDateString(undefined, { month: 'short' })} ${d.getDate()}`
}
