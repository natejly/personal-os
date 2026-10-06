import { useMemo, type CSSProperties } from 'react'
import type { CalendarEvent, Todo } from '@shared/types'
import { addDays, dayKey, fmtTime, startOfWeek } from './CalendarWeek'

/** First cell of the 6x7 month grid: the Monday on or before the 1st. */
export const monthGridStart = (d: Date): Date => startOfWeek(new Date(d.getFullYear(), d.getMonth(), 1))

const MAX_ROWS = 4

export interface CalendarMonthProps {
  /** Any date inside the month shown. */
  month: Date
  events: CalendarEvent[]
  todos: Todo[]
  onOpen: (e: CalendarEvent) => void
  /** Clicking a day number or "+n more" zooms into that day's week. */
  onPickDay: (d: Date) => void
  colorOf?: (e: CalendarEvent) => string | null
}

export default function CalendarMonth({ month, events, todos, onOpen, onPickDay, colorOf }: CalendarMonthProps): JSX.Element {
  const cells = useMemo(() => {
    const s = monthGridStart(month)
    return Array.from({ length: 42 }, (_, i) => addDays(s, i))
  }, [month])

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

  const todayKey = dayKey(new Date())
  const tint = (e: CalendarEvent): CSSProperties | undefined => {
    const c = colorOf?.(e)
    return c ? ({ '--ev': c } as CSSProperties) : undefined
  }

  return (
    <div className="cal-month">
      <div className="cal-month-head">
        {cells.slice(0, 7).map((d) => <div key={dayKey(d)}>{d.toLocaleDateString(undefined, { weekday: 'short' })}</div>)}
      </div>
      <div className="cal-month-grid">
        {cells.map((d) => {
          const dk = dayKey(d)
          const evs = eventsByDay[dk] ?? []
          const tds = todosByDay[dk] ?? []
          const shownTodos = evs.length >= MAX_ROWS ? [] : tds.slice(0, MAX_ROWS - evs.length)
          const extra = evs.length + tds.length - evs.slice(0, MAX_ROWS).length - shownTodos.length
          return (
            <div key={dk} className={`cal-month-cell ${d.getMonth() !== month.getMonth() ? 'dim' : ''} ${dk === todayKey ? 'today' : ''}`}>
              <button className="dom" onClick={() => onPickDay(d)}>
                {d.getDate() === 1 ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : d.getDate()}
              </button>
              {evs.slice(0, MAX_ROWS).map((e) => (
                <button key={`${e.calendar_id ?? ''}:${e.id}`} className={`cal-mchip ${e.all_day ? 'allday' : ''}`} style={tint(e)} title={e.summary} onClick={() => onOpen(e)}>
                  {!e.all_day && <><span className="cal-dot" /><span className="t">{fmtTime(new Date(e.start))}</span></>}
                  <span className="s">{e.summary || '(no title)'}</span>
                </button>
              ))}
              {shownTodos.map((t) => (
                <div key={t.id} className="cal-mchip todo" title="Todo due"><span className="s">○ {t.title}</span></div>
              ))}
              {extra > 0 && <button className="cal-month-more" onClick={() => onPickDay(d)}>+{extra} more</button>}
            </div>
          )
        })}
      </div>
    </div>
  )
}
