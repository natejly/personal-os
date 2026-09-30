import { useMemo, useState, type CSSProperties, type DragEvent } from 'react'
import { Plus, SlidersHorizontal } from 'lucide-react'
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

export interface CalendarWeekProps {
  /** One column per day, in order: seven for a week, one for a single day. */
  days: Date[]
  events: CalendarEvent[]
  todos: Todo[]
  /** Double-clicking a slot offers an inline create. */
  canCreate?: boolean
  onOpen: (e: CalendarEvent) => void
  onTodo?: (t: Todo) => void
  /** Drop a todo onto a day (hour null = all-day / due date only). */
  onTodoDrop?: (todoId: string, day: string, hour: number | null) => void
  /** Resolves true when the event was created, which is when the inline input clears. */
  onCreate?: (day: string, hour: number, title: string) => Promise<boolean>
  /** Open the full event editor instead of the quick inline create. */
  onCreateFull?: (day: string, hour: number, title: string) => void
  /** Per-event display color (Google event color or its calendar's). */
  colorOf?: (e: CalendarEvent) => string | null
}

/**
 * The day-column grid, shared by the Calendar page and the calendar widget so the two render the same
 * thing. The column count is inline because `.cal-grid` hard-codes seven.
 */
export default function CalendarWeek({ days, events, todos, canCreate = false, onOpen, onTodo, onTodoDrop, onCreate, onCreateFull, colorOf }: CalendarWeekProps): JSX.Element {
  const [creating, setCreating] = useState<{ day: string; hour: number } | null>(null)
  const [title, setTitle] = useState('')
  const [over, setOver] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)

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

  const create = async (): Promise<void> => {
    if (!creating || !onCreate || !title.trim()) return
    if (await onCreate(creating.day, creating.hour, title.trim())) { setTitle(''); setCreating(null) }
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
          <div key={'ad' + dk} className={`cal-allday ${over === `ad:${dk}` ? 'drop-over' : ''}`}
            title={onTodoDrop ? 'Drop a todo to due this day' : undefined}
            onDragOver={(e) => dragOver(e, `ad:${dk}`)}
            onDragLeave={() => setOver(null)}
            onDrop={(e) => dropTodo(e, dk, null)}>
            {(eventsByDay[dk] ?? []).filter((e) => e.all_day).map((e) => (
              <div key={e.id} className="cal-chip" style={colorOf?.(e) ? { background: `${colorOf(e)}38` } : undefined} onClick={() => onOpen(e)}>{e.summary}</div>
            ))}
            {(todosByDay[dk] ?? []).map((t) => (
              <div key={t.id} className={`cal-chip todo p${t.priority}`} title={onTodo ? 'Open todo' : 'Todo due'}
                onClick={() => onTodo?.(t)}>○ {t.title}</div>
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
          <div key={'col' + dk} className={`cal-col ${dk === todayKey ? 'today' : ''} ${over?.startsWith(dk + ':') ? 'drop-over' : ''}`} style={{ height: gridPx }}
            title={canCreate ? 'Double-click to add an event' : onTodoDrop ? 'Drop a todo to schedule it' : undefined}
            onDoubleClick={(e) => { if (!canCreate) return; setCreating({ day: dk, hour: hourAt(e.clientY, e.currentTarget.getBoundingClientRect()) }) }}
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
                <div key={e.id} className="cal-event" style={{ top, height: Math.min(h, gridPx - top), ...colorStyle(colorOf?.(e)) }} onClick={() => onOpen(e)} title={e.summary}>
                  <b>{e.summary}</b><span>{fmtTime(s)}</span>
                </div>
              )
            })}
            {creating?.day === dk && (
              <div className="cal-create" style={{ top: topOf(creating.hour) }}>
                <input autoFocus placeholder={`New event at ${creating.hour}:00`} value={title} onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') void create(); if (e.key === 'Escape') { setCreating(null); setTitle('') } }} />
                {onCreateFull && (
                  <button className="icon-btn" title="More options" onClick={() => { onCreateFull(creating.day, creating.hour, title.trim()); setCreating(null); setTitle('') }}>
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
