import { useMemo, useState, type DragEvent } from 'react'
import { Plus } from 'lucide-react'
import type { CalendarEvent, Todo } from '@shared/types'
import { hasDrag, readDrag } from '../canvas/dnd'

export const HOUR_PX = 44
export const startOfWeek = (d: Date): Date => { const x = new Date(d); x.setHours(0, 0, 0, 0); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x }
export const addDays = (d: Date, n: number): Date => { const x = new Date(d); x.setDate(x.getDate() + n); return x }
export const dayKey = (d: Date): string => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
/** Local calendar date (not UTC). `toISOString().slice(0,10)` is wrong near midnight. */
export const localDay = (d: Date = new Date()): string => dayKey(d)
export const fmtTime = (d: Date): string => d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })

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
}

/**
 * The day-column grid, shared by the Calendar page and the calendar widget so the two render the same
 * thing. The column count is inline because `.cal-grid` hard-codes seven.
 */
export default function CalendarWeek({ days, events, todos, canCreate = false, onOpen, onTodo, onTodoDrop, onCreate }: CalendarWeekProps): JSX.Element {
  const [creating, setCreating] = useState<{ day: string; hour: number } | null>(null)
  const [title, setTitle] = useState('')
  const [over, setOver] = useState<string | null>(null)

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
  const nowTop = (new Date().getHours() + new Date().getMinutes() / 60) * HOUR_PX

  return (
    <div className="cal-grid" style={{ gridTemplateColumns: `56px repeat(${days.length}, 1fr)`, minWidth: days.length > 1 ? 760 : 200 }}>
      <div className="cal-corner" />
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
            {(eventsByDay[dk] ?? []).filter((e) => e.all_day).map((e) => <div key={e.id} className="cal-chip" onClick={() => onOpen(e)}>{e.summary}</div>)}
            {(todosByDay[dk] ?? []).map((t) => (
              <div key={t.id} className={`cal-chip todo p${t.priority}`} title={onTodo ? 'Open todo' : 'Todo due'}
                onClick={() => onTodo?.(t)}>○ {t.title}</div>
            ))}
          </div>
        )
      })}
      <div className="cal-hours">
        {Array.from({ length: 24 }, (_, h) => <div key={h} className="cal-hour" style={{ height: HOUR_PX }}>{h === 0 ? '' : `${h % 12 || 12}${h < 12 ? 'am' : 'pm'}`}</div>)}
      </div>
      {days.map((d) => {
        const dk = dayKey(d)
        return (
          <div key={'col' + dk} className={`cal-col ${dk === todayKey ? 'today' : ''} ${over?.startsWith(dk + ':') ? 'drop-over' : ''}`} style={{ height: 24 * HOUR_PX }}
            title={canCreate ? 'Double-click to add an event' : onTodoDrop ? 'Drop a todo to schedule it' : undefined}
            onDoubleClick={(e) => { if (!canCreate) return; const rect = e.currentTarget.getBoundingClientRect(); setCreating({ day: dk, hour: Math.floor((e.clientY - rect.top) / HOUR_PX) }) }}
            onDragOver={(e) => {
              if (!onTodoDrop || !hasDrag(e.dataTransfer)) return
              const rect = e.currentTarget.getBoundingClientRect()
              dragOver(e, `${dk}:${Math.floor((e.clientY - rect.top) / HOUR_PX)}`)
            }}
            onDragLeave={() => setOver(null)}
            onDrop={(e) => {
              const rect = e.currentTarget.getBoundingClientRect()
              dropTodo(e, dk, Math.floor((e.clientY - rect.top) / HOUR_PX))
            }}>
            {Array.from({ length: 24 }, (_, h) => <div key={h} className="cal-line" style={{ top: h * HOUR_PX }} />)}
            {dk === todayKey && <div className="cal-now" style={{ top: nowTop }} />}
            {(eventsByDay[dk] ?? []).filter((e) => !e.all_day).map((e) => {
              const s = new Date(e.start), en = new Date(e.end)
              const top = (s.getHours() + s.getMinutes() / 60) * HOUR_PX
              const h = Math.max(22, ((en.getTime() - s.getTime()) / 3_600_000) * HOUR_PX - 2)
              return (
                <div key={e.id} className="cal-event" style={{ top, height: h }} onClick={() => onOpen(e)} title={e.summary}>
                  <b>{e.summary}</b><span>{fmtTime(s)}</span>
                </div>
              )
            })}
            {creating?.day === dk && (
              <div className="cal-create" style={{ top: creating.hour * HOUR_PX }}>
                <input autoFocus placeholder={`New event at ${creating.hour}:00`} value={title} onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') void create(); if (e.key === 'Escape') { setCreating(null); setTitle('') } }} />
                <button className="icon-btn" onClick={() => void create()}><Plus size={13} /></button>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
