import { useMemo, useRef, useState, type CSSProperties, type DragEvent, type MouseEvent as ReactMouseEvent } from 'react'
import { Plus, SlidersHorizontal } from 'lucide-react'
import type { CalendarEvent, Todo } from '@shared/types'
import { hasDrag, readDrag } from '../canvas/dnd'

/** Tint an event block with its Google color (falls back to the stylesheet blue). */
const colorStyle = (hex: string | null | undefined): CSSProperties | undefined =>
  hex ? { background: `${hex}38`, borderLeftColor: hex } : undefined

export const HOUR_PX = 44
/** Dragging snaps to this, like Google Calendar's own quarter-hour grid. */
export const SNAP_MIN = 15
/** A drag has to travel this far before it counts as one, so a plain click still opens an event. */
const DRAG_PX = 4
/** Blocks shorter than this put the time after the title instead of under it. */
const COMPACT_PX = 34
/** The grab strip along an event's bottom edge that resizes instead of moving. */
const RESIZE_PX = 7
/** Nothing scheduled: show a plain working day rather than a wall of empty night hours. */
const DEFAULT_WINDOW = { start: 8, end: 20 }
/** Never crop below this, so one short meeting does not leave a sliver of a grid. */
const MIN_HOURS = 6
const FULL_DAY = { start: 0, end: 24 }

const pad = (n: number): string => String(n).padStart(2, '0')

export const startOfWeek = (d: Date): Date => { const x = new Date(d); x.setHours(0, 0, 0, 0); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x }
export const addDays = (d: Date, n: number): Date => { const x = new Date(d); x.setDate(x.getDate() + n); return x }
export const dayKey = (d: Date): string => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
/** Local calendar date (not UTC). `toISOString().slice(0,10)` is wrong near midnight. */
export const localDay = (d: Date = new Date()): string => dayKey(d)
export const fmtTime = (d: Date): string => d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })

/** Round minutes-from-midnight onto the drag grid. */
export const snapMin = (min: number, step = SNAP_MIN): number => Math.round(min / step) * step

/**
 * A local wall-clock ISO string (no zone, so the backend reads it in the user's own timezone) for
 * `min` minutes after midnight on `day`. Minutes past 1440 roll into the next day, which is how an
 * event that ends at or after midnight has to be written.
 */
export const slotIso = (day: string, min: number): string => {
  const d = new Date(`${day}T00:00:00`)
  d.setMinutes(d.getMinutes() + Math.max(0, Math.round(min)))
  return `${dayKey(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}:00`
}
/** Clock label for a minute offset on a day, for drag previews and placeholders. */
export const fmtMin = (day: string, min: number): string => fmtTime(new Date(slotIso(day, min)))

/** A span of a single day, as dragged out on the grid or double-clicked. */
export interface Slot {
  day: string
  /** Minutes from midnight, local. */
  startMin: number
  endMin: number
}

/**
 * The narrowest whole-hour band that still holds every timed event on the days shown, padded by an
 * hour on each side. Days with only all-day events (or none at all) fall back to working hours.
 */
export function hourWindow(events: CalendarEvent[], days: Date[]): { start: number; end: number } {
  const shown = new Set(days.map(dayKey))
  let min = 24
  let max = 0
  for (const e of events) {
    if (e.all_day) continue
    const s = new Date(e.start)
    if (!shown.has(dayKey(s))) continue
    const en = new Date(e.end)
    // An event running past midnight pins the window to the end of its own day.
    const endHour = dayKey(en) === dayKey(s) ? en.getHours() + (en.getMinutes() > 0 ? 1 : 0) : 24
    min = Math.min(min, s.getHours())
    max = Math.max(max, endHour, s.getHours() + 1)
  }
  if (min >= max) return DEFAULT_WINDOW
  let start = Math.max(0, min - 1)
  let end = Math.min(24, max + 1)
  while (end - start < MIN_HOURS && (start > 0 || end < 24)) {
    if (start > 0) start -= 1
    if (end - start < MIN_HOURS && end < 24) end += 1
  }
  return { start, end }
}

export interface CalendarWeekProps {
  /** One column per day, in order: seven for a week, one for a single day. */
  days: Date[]
  events: CalendarEvent[]
  todos: Todo[]
  /** Dragging out a span (or double-clicking a slot) offers an inline create. */
  canCreate?: boolean
  onOpen: (e: CalendarEvent) => void
  onTodo?: (t: Todo) => void
  /** Drop a todo onto a day (hour null = all-day / due date only). */
  onTodoDrop?: (todoId: string, day: string, hour: number | null) => void
  /** Resolves true when the event was created, which is when the inline input clears. */
  onCreate?: (slot: Slot, title: string) => Promise<boolean>
  /** Open the full event editor instead of the quick inline create. */
  onCreateFull?: (slot: Slot, title: string) => void
  /** Dragging an event block moved or resized it; both ends are local wall-clock ISO strings. */
  onMove?: (e: CalendarEvent, startIso: string, endIso: string) => void
  /** Click an empty all-day cell. */
  onCreateAllDay?: (day: string) => void
  /** Per-event display color (Google event color or its calendar's). */
  colorOf?: (e: CalendarEvent) => string | null
}

/** A create drag in progress: the slot it started in and the edge the pointer is on. */
interface Selection { day: string; anchorMin: number; edgeMin: number }
/** A move or resize in progress: where the block is being drawn right now. */
interface Drag { id: string; event: CalendarEvent; day: string; startMin: number; endMin: number; resize: boolean }

/** The slot a create drag covers, anchor slot included whichever way the pointer went. */
export const selectionSlot = (s: Selection): Slot => ({
  day: s.day,
  startMin: Math.min(s.anchorMin, s.edgeMin),
  endMin: Math.max(s.anchorMin + SNAP_MIN, s.edgeMin)
})

/** A span of minutes inside one day. */
export interface Span { startMin: number; endMin: number }

/**
 * Where a move drag leaves a block: the same duration, snapped to the grid, and kept inside the
 * hours on screen — the band is cropped, so dropping a block outside it would hide it.
 */
export function movedSpan(startMin: number, durMin: number, deltaMin: number, band: { top: number; bottom: number }): Span {
  const start = Math.min(band.bottom - SNAP_MIN, Math.max(band.top, snapMin(startMin + deltaMin)))
  return { startMin: start, endMin: start + durMin }
}

/** Where a resize drag leaves the bottom edge: never above its own start, never past the band. */
export function resizedSpan(startMin: number, endMin: number, deltaMin: number, bottom: number): Span {
  return { startMin, endMin: Math.min(bottom, Math.max(startMin + SNAP_MIN, snapMin(endMin + deltaMin))) }
}

/**
 * The day-column grid, shared by the Calendar page and the calendar widget so the two render the same
 * thing. The column count is inline because `.cal-grid` hard-codes seven.
 */
/** Drop the events that are only a todo's own all-day mirror (see backend todocal.py).
 *
 * Both grids already draw a due todo as its own chip, so leaving the mirrored event in would
 * show every dated todo twice. A todo given a time keeps its block: that time is the point of
 * dragging it onto an hour, and the chip and the block say different things.
 */
export function withoutTodoEvents(events: CalendarEvent[], todos: Todo[]): CalendarEvent[] {
  const mirrored = new Set(todos.map((t) => t.calendar_event_id).filter((id): id is string => !!id))
  if (mirrored.size === 0) return events
  return events.filter((e) => !(e.all_day && e.id && mirrored.has(e.id)))
}

export default function CalendarWeek({ days, events, todos, canCreate = false, onOpen, onTodo, onTodoDrop, onCreate, onCreateFull, onMove, onCreateAllDay, colorOf }: CalendarWeekProps): JSX.Element {
  const [creating, setCreating] = useState<Slot | null>(null)
  const [title, setTitle] = useState('')
  const [over, setOver] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)
  const [sel, setSel] = useState<Selection | null>(null)
  const [drag, setDrag] = useState<Drag | null>(null)
  const colRefs = useRef<Record<string, HTMLDivElement | null>>({})
  /** A drag that actually moved must not also count as a click opening the event. */
  const draggedRef = useRef(false)

  const fitted = useMemo(() => hourWindow(events, days), [events, days])
  const { start: startHour, end: endHour } = showAll ? FULL_DAY : fitted
  const hours = endHour - startHour
  const gridPx = hours * HOUR_PX
  const minTop = startHour * 60
  const minBottom = endHour * 60
  /** Hours are cropped, so an offset inside a column is not the hour of the day. */
  const hourAt = (clientY: number, rect: DOMRect): number =>
    Math.min(endHour - 1, Math.max(startHour, startHour + Math.floor((clientY - rect.top) / HOUR_PX)))
  const topOf = (hour: number): number => (hour - startHour) * HOUR_PX
  const topOfMin = (min: number): number => topOf(min / 60)
  const clampMin = (min: number): number => Math.min(minBottom, Math.max(minTop, min))
  /** Minutes from midnight at a pointer position inside a column, on the drag grid. */
  const minuteAt = (clientY: number, rect: DOMRect): number =>
    clampMin(snapMin(minTop + ((clientY - rect.top) / HOUR_PX) * 60))
  /** Which day column the pointer is over, so a move can cross days. */
  const dayAt = (clientX: number, fallback: string): string => {
    for (const [dk, el] of Object.entries(colRefs.current)) {
      if (!el) continue
      const r = el.getBoundingClientRect()
      if (clientX >= r.left && clientX <= r.right) return dk
    }
    return fallback
  }

  const eventsByDay = useMemo(() => {
    const m: Record<string, CalendarEvent[]> = {}
    for (const e of events) {
      const d = e.all_day ? e.start : dayKey(new Date(e.start))
      ;(m[d] ??= []).push(e)
    }
    return m
  }, [events])
  const todosByDay = useMemo(() => {
    const m: Record<string, Todo[]> = {}
    for (const t of todos) if (t.due && !t.done) (m[t.due] ??= []).push(t)
    return m
  }, [todos])

  const openCreate = (slot: Slot): void => { setTitle(''); setCreating(slot) }
  const create = async (): Promise<void> => {
    if (!creating || !onCreate || !title.trim()) return
    if (await onCreate(creating, title.trim())) { setTitle(''); setCreating(null) }
  }

  /** Press-and-drag on empty grid: pull out a span, then name it in the inline create. */
  const beginSelect = (e: ReactMouseEvent<HTMLDivElement>, dk: string): void => {
    if (!canCreate || !onCreate || e.button !== 0) return
    const rect = e.currentTarget.getBoundingClientRect()
    // The slot pressed in is the anchor, so it is floored rather than rounded, and never the last
    // one: a drag that starts at the very bottom still has a slot's worth of room to cover.
    const anchorMin = Math.min(minBottom - SNAP_MIN, clampMin(Math.floor((minTop + ((e.clientY - rect.top) / HOUR_PX) * 60) / SNAP_MIN) * SNAP_MIN))
    const y0 = e.clientY
    let cur: Selection | null = null
    const onMouseMove = (m: MouseEvent): void => {
      if (!cur && Math.abs(m.clientY - y0) < DRAG_PX) return
      cur = { day: dk, anchorMin, edgeMin: minuteAt(m.clientY, rect) }
      setSel(cur)
    }
    const onMouseUp = (): void => {
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
      setSel(null)
      if (cur) openCreate(selectionSlot(cur))
    }
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
  }

  /** Press-and-drag on an event block: move it (any day), or resize it from its bottom edge. */
  const beginDrag = (e: ReactMouseEvent<HTMLDivElement>, ev: CalendarEvent, resize: boolean): void => {
    if (!onMove || e.button !== 0) return
    e.stopPropagation()
    draggedRef.current = false
    const s = new Date(ev.start), en = new Date(ev.end)
    const day0 = dayKey(s)
    const startMin0 = s.getHours() * 60 + s.getMinutes()
    const durMin = Math.max(SNAP_MIN, Math.round((en.getTime() - s.getTime()) / 60_000))
    const endMin0 = startMin0 + durMin
    const x0 = e.clientX, y0 = e.clientY
    let cur: Drag | null = null
    const onMouseMove = (m: MouseEvent): void => {
      if (!cur && Math.abs(m.clientY - y0) < DRAG_PX && Math.abs(m.clientX - x0) < DRAG_PX) return
      const deltaMin = snapMin(((m.clientY - y0) / HOUR_PX) * 60)
      const span = resize
        ? resizedSpan(startMin0, endMin0, deltaMin, minBottom)
        : movedSpan(startMin0, durMin, deltaMin, { top: minTop, bottom: minBottom })
      cur = { id: ev.id, event: ev, day: resize ? day0 : dayAt(m.clientX, day0), ...span, resize }
      setDrag(cur)
    }
    const onMouseUp = (): void => {
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
      setDrag(null)
      if (!cur) return
      draggedRef.current = true
      if (cur.day === day0 && cur.startMin === startMin0 && cur.endMin === endMin0) return
      onMove(ev, slotIso(cur.day, cur.startMin), slotIso(cur.day, cur.endMin))
    }
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
  }

  const dragOver = (e: DragEvent<HTMLDivElement>, slot: string): void => {
    if (!onTodoDrop || !hasDrag(e.dataTransfer)) return
    e.preventDefault()
    e.stopPropagation()
    e.dataTransfer.dropEffect = 'copy'
    setOver(slot)
  }
  const dropTodo = (e: DragEvent<HTMLDivElement>, day: string, hour: number | null): void => {
    setOver(null)
    if (!onTodoDrop) return
    const p = readDrag(e.dataTransfer)
    if (p?.kind !== 'todo') return
    e.preventDefault()
    e.stopPropagation()
    onTodoDrop(p.id, day, hour)
  }

  const todayKey = localDay()
  const now = new Date()
  const nowHour = now.getHours() + now.getMinutes() / 60
  const nowVisible = nowHour >= startHour && nowHour <= endHour

  return (
    <div className={`cal-grid ${sel || drag ? 'dragging' : ''}`} style={{ gridTemplateColumns: `56px repeat(${days.length}, 1fr)`, minWidth: days.length > 1 ? 760 : 200 }}>
      <div className="cal-corner">
        {(showAll || hours < 24) && (
          <button className="cal-hours-toggle" title={showAll ? 'Crop to the hours with events' : 'Show all 24 hours'}
            onClick={() => setShowAll(!showAll)}>{showAll ? 'fit' : '24h'}</button>
        )}
      </div>
      {days.map((d) => (
        <div key={dayKey(d)} className={`cal-dayhead ${dayKey(d) === todayKey ? 'today' : ''}`}>
          <span className="dow">{d.toLocaleDateString(undefined, { weekday: 'short' })}</span>
          <span className="dom">{d.getDate()}</span>
        </div>
      ))}
      <div className="cal-allday-label">all day</div>
      {days.map((d) => {
        const dk = dayKey(d)
        return (
          <div key={'ad' + dk} className={`cal-allday ${over === `ad:${dk}` ? 'drop-over' : ''} ${canCreate && onCreateAllDay ? 'can-create' : ''}`}
            title={[canCreate && onCreateAllDay ? 'Click to add an all-day event' : '', onTodoDrop ? 'Drop a todo to due this day' : ''].filter(Boolean).join(' · ') || undefined}
            onClick={(e) => {
              if (!canCreate || !onCreateAllDay) return
              if ((e.target as HTMLElement).closest('.cal-chip')) return
              onCreateAllDay(dk)
            }}
            onDragOver={(e) => dragOver(e, `ad:${dk}`)}
            onDragLeave={() => setOver(null)}
            onDrop={(e) => dropTodo(e, dk, null)}>
            {(eventsByDay[dk] ?? []).filter((e) => e.all_day).map((e) => (
              <div key={e.id} className="cal-chip" style={colorOf?.(e) ? { background: `${colorOf(e)}38` } : undefined} onClick={(ev) => { ev.stopPropagation(); onOpen(e) }}>{e.summary}</div>
            ))}
            {(todosByDay[dk] ?? []).map((t) => (
              <div key={t.id} className={`cal-chip todo p${t.priority}`} title={onTodo ? 'Open todo' : 'Todo due'}
                onClick={(ev) => { ev.stopPropagation(); onTodo?.(t) }}>○ {t.title}</div>
            ))}
          </div>
        )
      })}
      <div className="cal-hours">
        {Array.from({ length: hours }, (_, i) => startHour + i).map((h) => (
          <div key={h} className="cal-hour" style={{ height: HOUR_PX }}>{h === 0 ? '' : `${h % 12 || 12}${h < 12 ? 'am' : 'pm'}`}</div>
        ))}
      </div>
      {days.map((d) => {
        const dk = dayKey(d)
        const timed = (eventsByDay[dk] ?? []).filter((e) => !e.all_day)
        // A block being dragged follows the pointer, so it leaves its own column and joins another.
        const shown = !drag ? timed
          : timed.some((e) => e.id === drag.id)
            ? (drag.day === dk ? timed : timed.filter((e) => e.id !== drag.id))
            : (drag.day === dk ? [...timed, drag.event] : timed)
        const selSlot = sel && sel.day === dk ? selectionSlot(sel) : null
        return (
          <div key={'col' + dk} ref={(el) => { if (el) colRefs.current[dk] = el; else delete colRefs.current[dk] }}
            className={`cal-col ${dk === todayKey ? 'today' : ''} ${over?.startsWith(dk + ':') ? 'drop-over' : ''}`} style={{ height: gridPx }}
            title={canCreate ? 'Drag to add an event' : onTodoDrop ? 'Drop a todo to schedule it' : undefined}
            onMouseDown={(e) => beginSelect(e, dk)}
            onDoubleClick={(e) => { if (!canCreate) return; const h = hourAt(e.clientY, e.currentTarget.getBoundingClientRect()); openCreate({ day: dk, startMin: h * 60, endMin: h * 60 + 60 }) }}
            onDragOver={(e) => {
              if (!onTodoDrop || !hasDrag(e.dataTransfer)) return
              dragOver(e, `${dk}:${hourAt(e.clientY, e.currentTarget.getBoundingClientRect())}`)
            }}
            onDragLeave={() => setOver(null)}
            onDrop={(e) => dropTodo(e, dk, hourAt(e.clientY, e.currentTarget.getBoundingClientRect()))}>
            {Array.from({ length: hours }, (_, i) => <div key={i} className="cal-line" style={{ top: i * HOUR_PX }} />)}
            {dk === todayKey && nowVisible && <div className="cal-now" style={{ top: topOf(nowHour) }} />}
            {selSlot && (
              <div className="cal-sel" style={{ top: topOfMin(selSlot.startMin), height: Math.max(12, topOfMin(selSlot.endMin) - topOfMin(selSlot.startMin)) }}>
                <span>{fmtMin(dk, selSlot.startMin)} – {fmtMin(dk, selSlot.endMin)}</span>
              </div>
            )}
            {shown.map((e) => {
              const dragging = drag?.id === e.id
              const s = new Date(e.start), en = new Date(e.end)
              const startMin = dragging ? drag.startMin : s.getHours() * 60 + s.getMinutes()
              const endMin = dragging ? drag.endMin : startMin + Math.max(SNAP_MIN, Math.round((en.getTime() - s.getTime()) / 60_000))
              const top = topOfMin(startMin)
              const h = Math.max(22, topOfMin(endMin) - top - 2)
              const height = Math.min(h, gridPx - top)
              // Too short for two lines: the time reads better after the title than under it.
              const compact = height < COMPACT_PX
              return (
                <div key={e.id} className={`cal-event ${onMove ? 'movable' : ''} ${compact ? 'compact' : ''} ${dragging ? 'dragging' : ''}`}
                  style={{ top, height, ...colorStyle(colorOf?.(e)) }}
                  onMouseDown={(ev) => beginDrag(ev, e, false)}
                  onClick={() => { if (draggedRef.current) { draggedRef.current = false; return } onOpen(e) }}
                  title={onMove ? `${e.summary} — drag to move, drag the bottom edge to resize` : e.summary}>
                  <b>{e.summary}</b><span>{fmtMin(dk, startMin)}</span>
                  {onMove && height >= 22 && dayKey(en) === dayKey(s) && (
                    <div className="cal-event-grip" style={{ height: RESIZE_PX }} onMouseDown={(ev) => beginDrag(ev, e, true)} />
                  )}
                </div>
              )
            })}
            {creating?.day === dk && (
              <div className="cal-create" style={{ top: topOfMin(creating.startMin) }} onMouseDown={(e) => e.stopPropagation()}>
                <input autoFocus placeholder={`New event ${fmtMin(dk, creating.startMin)} – ${fmtMin(dk, creating.endMin)}`} value={title} onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') void create(); if (e.key === 'Escape') { setCreating(null); setTitle('') } }} />
                {onCreateFull && (
                  <button className="icon-btn" title="More options" onClick={() => { onCreateFull(creating, title.trim()); setCreating(null); setTitle('') }}>
                    <SlidersHorizontal size={13} />
                  </button>
                )}
                <button className="icon-btn" title="Add" onClick={() => void create()}><Plus size={13} /></button>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
