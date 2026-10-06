import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { Calendar as CalIcon, ChevronLeft, ChevronRight, Plus } from 'lucide-react'
import type { CalendarEvent, DragKind, Todo } from '@shared/types'
import { useStore } from '../../store'
import { PIM_SETTINGS_TAB, pimConnected, pimLabel } from '../../lib/pim'
import { useCanvas } from '../store'
import { api } from '../../lib/api'
import CalendarWeek, { addDays, dayKey, fmtTime, localDay, slotIso, startOfWeek, withoutTodoEvents, type Slot } from '../../components/CalendarWeek'
import EventEditor, { eventColor, primeCalendarMeta, type EventDraft } from '../../components/EventEditor'
import { useVisibleCalendars } from '../../components/useVisibleCalendars'
import { scheduleTodo } from '../../components/TodoItem'
import { hasDrag, readDrag, useDropTarget } from '../dnd'
import { calendarViewKey, readView, writeView } from '../../lib/viewCache'
import type { WidgetDef, WidgetProps } from '../registry'

type CalMode = 'agenda' | 'day' | 'week'

const MODES: CalMode[] = ['agenda', 'day', 'week']
const DAY_CHOICES = [1, 3, 7, 14]
const POLL_MS = 120_000
const ACCEPTS: DragKind[] = ['todo']
const readMode = (v: unknown): CalMode => (MODES.includes(v as CalMode) ? (v as CalMode) : 'agenda')

const CalendarWidget = ({ window: win, live, onConfig }: WidgetProps): JSX.Element => {
  const connected = useStore(pimConnected)
  const label = useStore(pimLabel)
  const todos = useStore((s) => s.todos)
  const toast = useStore((s) => s.toast)
  const openTodos = (): void => void useCanvas.getState().ensureWindow(win.canvas_id, 'todos')
  const updateTodo = useStore((s) => s.updateTodo)
  const mode = readMode(win.config.mode)
  const days = typeof win.config.days === 'number' ? win.config.days : 7
  const [shift, setShift] = useState(0)
  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<{ event: CalendarEvent | null; draft?: EventDraft } | null>(null)
  const { query, visibleIds, ready, calendars } = useVisibleCalendars()
  const [, setMetaTick] = useState(0)

  const anchor = useMemo(() => (mode === 'week' ? addDays(startOfWeek(new Date()), shift * 7) : addDays(new Date(), shift)), [mode, shift])
  const span = mode === 'week' ? 7 : mode === 'day' ? 1 : days
  const columns = useMemo(() => (mode === 'agenda' ? [] : Array.from({ length: span }, (_, i) => addDays(anchor, i))), [mode, span, anchor])
  const startIso = anchor.toISOString()
  const cacheKey = connected && query ? calendarViewKey(mode === 'agenda' ? 'now' : startIso, span, query) : ''
  const [painted, setPainted] = useState(cacheKey)
  if (cacheKey !== painted) {
    setPainted(cacheKey)
    if (!query) setEvents([])
    else setEvents(readView<CalendarEvent[]>(cacheKey) ?? [])
  }

  // Newest reload wins: one started for a range the widget has since left must not paint (the
  // poller effect below bumps it whenever the range changes).
  const reloadSeq = useRef(0)

  useEffect(() => {
    if (!live) return
    void useStore.getState().refreshTodos('all', false)
  }, [live])

  // Off-screen, minimized or zoomed out: stop the poller so a hidden window does not hit Google.
  useEffect(() => {
    reloadSeq.current++
    if (!live || !connected || query == null) return
    if (!query) { setEvents([]); return }
    let alive = true
    const key = calendarViewKey(mode === 'agenda' ? 'now' : startIso, span, query)
    const pull = async (): Promise<void> => {
      try {
        const list = mode === 'agenda' ? await api.google.calendar(span, query) : await api.google.calendarRange(startIso, span, query)
        if (!alive) return
        writeView(key, list)
        setEvents(list)
        setError(null)
      } catch (e) {
        if (alive) setError((e as Error).message)
      }
    }
    void pull()
    void primeCalendarMeta().then(() => { if (alive) setMetaTick((t) => t + 1) })
    const t = setInterval(() => void pull(), POLL_MS)
    return () => { alive = false; clearInterval(t) }
  }, [live, connected, mode, span, startIso, query])

  const reload = async (): Promise<void> => {
    if (!query) { setEvents([]); return }
    const mine = ++reloadSeq.current
    const list = mode === 'agenda' ? await api.google.calendar(span, query) : await api.google.calendarRange(startIso, span, query)
    writeView(calendarViewKey(mode === 'agenda' ? 'now' : startIso, span, query), list)
    if (reloadSeq.current === mine) setEvents(list)
  }

  const dropTodo = async (todoId: string, day: string, hour: number | null): Promise<void> => {
    const todo = useStore.getState().todos.find((t) => t.id === todoId)
    if (!todo) return
    try {
      // scheduleTodo writes the due date with the event link, so the mirror never sees one without the other.
      if (!connected) await updateTodo(todoId, { due: day })
      else {
        const start = hour === null ? day : `${day}T${String(hour).padStart(2, '0')}:00:00`
        await scheduleTodo(todo, start)
        if (hour !== null) await reload()
      }
      toast(hour === null ? `Due ${day}` : `Scheduled ${hour}:00`)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const create = async (slot: Slot, title: string): Promise<boolean> => {
    try {
      await api.google.createEvent({ summary: title, start: slotIso(slot.day, slot.startMin), end: slotIso(slot.day, slot.endMin) })
      toast(`Added "${title}"`)
      await reload()
      return true
    } catch (e) {
      toast((e as Error).message, 'error')
      return false
    }
  }

  /** Drag-moved or resized a block: patch just its start and end, then re-read the week. */
  const move = async (e: CalendarEvent, start: string, end: string): Promise<void> => {
    // A new time for a meeting is news to its guests, so ask before mailing them, as Google does.
    const send_updates = e.attendees.length > 0 && window.confirm(`Email the ${e.attendees.length} guest${e.attendees.length > 1 ? 's' : ''} about the new time?`) ? 'all' : 'none'
    const before = events
    setEvents((list) => list.map((x) => (x.id === e.id ? { ...x, start: new Date(start).toISOString(), end: new Date(end).toISOString() } : x)))
    try {
      await api.google.updateEvent(e.id, { start, end, calendar_id: e.calendar_id ?? 'primary', send_updates })
      toast(`Moved to ${new Date(start).toLocaleString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' })}`)
      await reload()
    } catch (err) {
      setEvents(before)
      toast((err as Error).message, 'error')
    }
  }

  const drop = useDropTarget(ACCEPTS, (p) => {
    if (p?.kind === 'todo') void dropTodo(p.id, localDay(), null)
  })

  const agendaDrop = (e: DragEvent<HTMLDivElement>, day: string): void => {
    const p = readDrag(e.dataTransfer)
    if (p?.kind !== 'todo') return
    e.preventDefault()
    e.stopPropagation()
    void dropTodo(p.id, day, null)
  }

  const shown = useMemo(() => {
    const base = withoutTodoEvents(events, todos)
    if (!ready || calendars.length === 0) return base
    const ids = new Set(visibleIds)
    return base.filter((e) => !e.calendar_id || ids.has(e.calendar_id))
  }, [events, todos, ready, calendars.length, visibleIds])

  const agenda = useMemo(() => {
    if (mode !== 'agenda') return []
    const m = new Map<string, { events: CalendarEvent[]; todos: Todo[] }>()
    const slot = (k: string): { events: CalendarEvent[]; todos: Todo[] } => {
      const cur = m.get(k) ?? { events: [], todos: [] }
      m.set(k, cur)
      return cur
    }
    for (const e of shown) slot(e.all_day ? e.start : dayKey(new Date(e.start))).events.push(e)
    const last = dayKey(addDays(new Date(), span - 1))
    for (const t of todos) if (t.due && !t.done && t.due <= last && t.due >= localDay()) slot(t.due).todos.push(t)
    return [...m.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))
  }, [mode, shown, todos, span])

  if (!live) {
    return (
      <div className="proxy-card">
        <CalIcon size={18} />
        <strong>{win.title || 'Calendar'}</strong>
        <span>Paused while off-screen</span>
      </div>
    )
  }

  return (
    <div className={drop.over ? 'widget drop-over' : 'widget'} {...drop.handlers}>
      <div className="widget-bar">
        {MODES.map((m) => (
          <button key={m} className={`widget-chip ${m === mode ? 'on' : ''}`} onClick={() => { setShift(0); onConfig({ mode: m }) }}>{m}</button>
        ))}
        {mode === 'agenda' ? (
          <select className="widget-chip" value={days} onChange={(e) => onConfig({ days: Number(e.target.value) })}>
            {DAY_CHOICES.map((d) => <option key={d} value={d}>{d}d</option>)}
          </select>
        ) : (
          <>
            <button className="widget-chip" title="Back" onClick={() => setShift(shift - 1)}><ChevronLeft size={11} /></button>
            <button className="widget-chip" title="Today" onClick={() => setShift(0)}>{anchor.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</button>
            <button className="widget-chip" title="Forward" onClick={() => setShift(shift + 1)}><ChevronRight size={11} /></button>
          </>
        )}
        <span className="spacer" />
        {connected && <button className="widget-chip" title="New event" onClick={() => setEditing({ event: null, draft: {} })}><Plus size={11} /></button>}
        {!connected && <button className="widget-chip" onClick={() => useStore.getState().openSettings(PIM_SETTINGS_TAB)}>Connect {label}</button>}
        {error && <span className="widget-meta" title={error}>offline</span>}
      </div>

      {mode === 'agenda' ? (
        <div className="widget-scroll">
          {agenda.length === 0 && <div className="widget-empty"><span>Nothing in the next {span} days.</span></div>}
          <div className="widget-list">
            {agenda.map(([k, g]) => (
              <div key={k} className="agenda-day"
                onDragOver={(e) => { if (!hasDrag(e.dataTransfer)) return; e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect = 'copy' }}
                onDrop={(e) => agendaDrop(e, k)}>
                <div className="widget-meta">{new Date(`${k}T00:00:00`).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })}</div>
                {g.events.map((e) => (
                  <button key={e.id} className="widget-row" onClick={() => setEditing({ event: e })}>
                    <span className="widget-meta">{e.all_day ? 'all day' : fmtTime(new Date(e.start))}</span>
                    <span className="grow widget-title">{e.summary}</span>
                  </button>
                ))}
                {g.todos.map((t) => (
                  <button key={t.id} className="widget-row" onClick={openTodos}>
                    <span className="widget-meta">todo</span>
                    <span className="grow widget-sub">○ {t.title}</span>
                  </button>
                ))}
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="cal-scroll">
          <CalendarWeek days={columns} events={shown} todos={todos} canCreate={connected}
            onOpen={(e) => setEditing({ event: e })} onTodo={openTodos} onTodoDrop={(id, day, hour) => void dropTodo(id, day, hour)} onCreate={create}
            onCreateFull={(slot, title) => setEditing({ event: null, draft: { day: slot.day, title, start: slotIso(slot.day, slot.startMin), end: slotIso(slot.day, slot.endMin) } })}
            onCreateAllDay={(day) => setEditing({ event: null, draft: { day, allDay: true } })}
            onMove={connected ? (e, start, end) => void move(e, start, end) : undefined}
            colorOf={eventColor} />
        </div>
      )}

      {editing && (
        <EventEditor key={editing.event?.id ?? `new:${editing.draft?.day ?? ''}:${editing.draft?.hour ?? ''}:${editing.draft?.start ?? ''}:${editing.draft?.end ?? ''}:${editing.draft?.allDay ? 'day' : ''}`}
          event={editing.event} draft={editing.draft} onClose={() => setEditing(null)} onSaved={() => void reload().catch(() => undefined)} />
      )}
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'calendar',
  label: 'Calendar',
  icon: <CalIcon size={18} />,
  defaultSize: { w: 640, h: 520 },
  minSize: { w: 320, h: 280 },
  chrome: 'full',
  heavy: true,
  defaultConfig: { mode: 'agenda', days: 7 },
  accepts: ACCEPTS,
  Component: CalendarWidget
}
