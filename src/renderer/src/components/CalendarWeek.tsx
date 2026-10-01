import { useMemo, useRef, useState, type CSSProperties, type DragEvent, type PointerEvent as ReactPointerEvent } from 'react'
import type { CalendarEvent, Todo } from '@shared/types'
import { hasDrag, readDrag } from '../canvas/dnd'

/** Tint an event block with its Google color (falls back to the stylesheet blue). */
const colorStyle = (hex: string | null | undefined): CSSProperties | undefined =>
  hex ? { background: `${hex}38`, borderLeftColor: hex } : undefined

export const HOUR_PX = 44
/** Nothing scheduled: show a plain working day rather than a wall of empty night hours. */
const DEFAULT_WINDOW = { start: 8, end: 20 }
/** Never crop below this, so one short meeting does not leave a sliver of a grid. */
const MIN_HOURS = 6
const FULL_DAY = { start: 0, end: 24 }

export const startOfWeek = (d: Date): Date => { const x = new Date(d); x.setHours(0, 0, 0, 0); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x }
export const addDays = (d: Date, n: number): Date => { const x = new Date(d); x.setDate(x.getDate() + n); return x }
export const dayKey = (d: Date): string => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
/** Local calendar date (not UTC). `toISOString().slice(0,10)` is wrong near midnight. */
export const localDay = (d: Date = new Date()): string => dayKey(d)
export const fmtTime = (d: Date): string => d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })

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

/** Click-drag snaps to 15 minutes. A click with no drag is one hour, like Google Calendar. */
export const SLOT_MINUTES = 15
const CLICK_MINUTES = 60

/**
 * `anchor` and `pointer` are already snapped down to a slot. A drag includes the slot under the
 * pointer. A click near the end of the visible day shifts earlier so it still lasts an hour.
 */
export function selectionRange(anchor: number, pointer: number, dayEndMin: number): { start: number; end: number } {
  if (pointer === anchor) {
    let start = anchor
    let end = start + CLICK_MINUTES
    if (end > dayEndMin) {
      end = dayEndMin
      start = Math.max(0, end - CLICK_MINUTES)
    }
    return { start, end }
  }
  if (pointer > anchor) {
    const end = Math.min(dayEndMin, pointer + SLOT_MINUTES)
    return { start: anchor, end: Math.max(anchor + SLOT_MINUTES, end) }
  }
  const end = Math.min(dayEndMin, anchor + SLOT_MINUTES)
  return { start: pointer, end: Math.max(pointer + SLOT_MINUTES, end) }
}

export interface CalendarWeekProps {
  /** One column per day, in order: seven for a week, one for a single day. */
  days: Date[]
  events: CalendarEvent[]
  todos: Todo[]
  /** Click or drag an empty slot to create. */
  canCreate?: boolean
  onOpen: (e: CalendarEvent) => void
  onTodo?: (t: Todo) => void
  /** Drop a todo onto a day (hour null = all-day / due date only). */
  onTodoDrop?: (todoId: string, day: string, hour: number | null) => void
  /** Click or drag on a day column. Minutes are from midnight; `end` may be 24:00. */
  onSelectRange?: (day: string, startMin: number, endMin: number) => void
  /** Click an empty all-day cell. */
  onCreateAllDay?: (day: string) => void
  /** Per-event display color (Google event color or its calendar's). */
  colorOf?: (e: CalendarEvent) => string | null
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

export default function CalendarWeek({ days, events, todos, canCreate = false, onOpen, onTodo, onTodoDrop, onSelectRange, onCreateAllDay, colorOf }: CalendarWeekProps): JSX.Element {
  const [over, setOver] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)
  const [ghost, setGhost] = useState<{ day: string; start: number; end: number } | null>(null)
  const dragRef = useRef<{ day: string; anchor: number; el: HTMLDivElement } | null>(null)

  const fitted = useMemo(() => hourWindow(events, days), [events, days])
  const { start: startHour, end: endHour } = showAll ? FULL_DAY : fitted
  const hours = endHour - startHour
  const gridPx = hours * HOUR_PX
  /** Hours are cropped, so an offset inside a column is not the hour of the day. */
  const hourAt = (clientY: number, rect: DOMRect): number =>
    Math.min(endHour - 1, Math.max(startHour, startHour + Math.floor((clientY - rect.top) / HOUR_PX)))
  const topOf = (hour: number): number => (hour - startHour) * HOUR_PX

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

  const dayEndMin = endHour * 60
  const floorMinute = (clientY: number, rect: DOMRect): number => {
    const raw = startHour * 60 + ((clientY - rect.top) / HOUR_PX) * 60
    const floored = Math.floor(raw / SLOT_MINUTES) * SLOT_MINUTES
    return Math.min(dayEndMin - SLOT_MINUTES, Math.max(startHour * 60, floored))
  }
  const rangeAt = (clientY: number, el: HTMLDivElement, anchor: number): { start: number; end: number } =>
    selectionRange(anchor, floorMinute(clientY, el.getBoundingClientRect()), dayEndMin)
  const clock = (min: number): string => {
    const d = new Date()
    d.setHours(Math.floor(min / 60) % 24, min % 60, 0, 0)
    return fmtTime(d)
  }
  const beginDrag = (e: ReactPointerEvent<HTMLDivElement>, dk: string): void => {
    if (e.button !== 0 || !canCreate || !onSelectRange) return
    if ((e.target as HTMLElement).closest('.cal-event, .cal-chip, button, input, a')) return
    const anchor = floorMinute(e.clientY, e.currentTarget.getBoundingClientRect())
    dragRef.current = { day: dk, anchor, el: e.currentTarget }
    e.currentTarget.setPointerCapture(e.pointerId)
    setGhost({ day: dk, ...selectionRange(anchor, anchor, dayEndMin) })
  }
  const moveDrag = (e: ReactPointerEvent<HTMLDivElement>, dk: string): void => {
    const d = dragRef.current
    if (!d || d.day !== dk) return
    setGhost({ day: dk, ...rangeAt(e.clientY, d.el, d.anchor) })
  }
  const endDrag = (e: ReactPointerEvent<HTMLDivElement>, dk: string): void => {
    const d = dragRef.current
    if (!d || d.day !== dk) return
    dragRef.current = null
    const range = rangeAt(e.clientY, d.el, d.anchor)
    setGhost(null)
    onSelectRange?.(dk, range.start, range.end)
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
    <div className="cal-grid" style={{ gridTemplateColumns: `56px repeat(${days.length}, 1fr)`, minWidth: days.length > 1 ? 760 : 200 }}>
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
              <div key={e.id} className="cal-chip" style={colorOf?.(e) ? { background: `${colorOf(e)}38` } : undefined}
                onClick={(ev) => { ev.stopPropagation(); onOpen(e) }}>{e.summary}</div>
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
        return (
          <div key={'col' + dk} className={`cal-col ${dk === todayKey ? 'today' : ''} ${over?.startsWith(dk + ':') ? 'drop-over' : ''} ${canCreate && onSelectRange ? 'can-create' : ''}`} style={{ height: gridPx }}
            title={canCreate && onSelectRange ? 'Click or drag to add an event' : onTodoDrop ? 'Drop a todo to schedule it' : undefined}
            onPointerDown={(e) => beginDrag(e, dk)}
            onPointerMove={(e) => moveDrag(e, dk)}
            onPointerUp={(e) => endDrag(e, dk)}
            onPointerCancel={() => { if (dragRef.current?.day === dk) { dragRef.current = null; setGhost(null) } }}
            onDragOver={(e) => {
              if (!onTodoDrop || !hasDrag(e.dataTransfer)) return
              dragOver(e, `${dk}:${hourAt(e.clientY, e.currentTarget.getBoundingClientRect())}`)
            }}
            onDragLeave={() => setOver(null)}
            onDrop={(e) => dropTodo(e, dk, hourAt(e.clientY, e.currentTarget.getBoundingClientRect()))}>
            {Array.from({ length: hours }, (_, i) => <div key={i} className="cal-line" style={{ top: i * HOUR_PX }} />)}
            {dk === todayKey && nowVisible && <div className="cal-now" style={{ top: topOf(nowHour) }} />}
            {(eventsByDay[dk] ?? []).filter((e) => !e.all_day).map((e) => {
              const s = new Date(e.start), en = new Date(e.end)
              const top = topOf(s.getHours() + s.getMinutes() / 60)
              const h = Math.max(22, ((en.getTime() - s.getTime()) / 3_600_000) * HOUR_PX - 2)
              return (
                <div key={e.id} className="cal-event" style={{ top, height: Math.min(h, gridPx - top), ...colorStyle(colorOf?.(e)) }}
                  onPointerDown={(ev) => ev.stopPropagation()} onClick={() => onOpen(e)} title={e.summary}>
                  <b>{e.summary}</b><span>{fmtTime(s)}</span>
                </div>
              )
            })}
            {ghost?.day === dk && (
              <div className="cal-ghost" style={{ top: topOf(ghost.start / 60), height: Math.max(18, ((ghost.end - ghost.start) / 60) * HOUR_PX - 1) }}>
                {clock(ghost.start)} – {clock(ghost.end)}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
