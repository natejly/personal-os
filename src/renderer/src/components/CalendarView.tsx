import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronLeft, ChevronRight, PanelLeftOpen, Calendar as CalIcon, Plus, RefreshCw } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import SendToSpace from './SendToSpace'
import CalendarWeek, { addDays, fmtTime, slotIso, startOfWeek, withoutTodoEvents, type Slot } from './CalendarWeek'
import EventEditor, { eventColor, primeCalendarMeta, type EventDraft } from './EventEditor'
import { useVisibleCalendars } from './useVisibleCalendars'
import { scheduleTodo } from './TodoItem'
import type { CalendarEvent, GoogleCalendar } from '@shared/types'
import { oneLine } from '../lib/emailAsk'
import { lines, usePageContext } from '../lib/pageContext'
import { calendarViewKey, readView, writeView } from '../lib/viewCache'
import AppSwitcher from './AppSwitcher'

function CalToggle({ c, on, onToggle }: { c: GoogleCalendar; on: boolean; onToggle: () => void }): JSX.Element {
  const color = c.color ?? 'var(--accent-solid)'
  return (
    <button type="button" className={on ? 'cal-cal on' : 'cal-cal'} aria-pressed={on}
      title={on ? `Hide ${c.summary}` : `Show ${c.summary}`} onClick={onToggle}>
      <span className="cal-cal-box" style={{ borderColor: color, background: on ? color : 'transparent' }}>
        {on && <Check size={11} strokeWidth={3} />}
      </span>
      <span className="cal-cal-name">{c.summary}</span>
    </button>
  )
}

function CalendarRail({ calendars, ready, shown, toggle }: {
  calendars: GoogleCalendar[]
  ready: boolean
  shown: (c: GoogleCalendar) => boolean
  toggle: (c: GoogleCalendar) => void
}): JSX.Element {
  const mine = calendars.filter((c) => c.primary || c.access_role === 'owner' || c.access_role === 'writer')
  const mineIds = new Set(mine.map((c) => c.id))
  const other = calendars.filter((c) => !mineIds.has(c.id))
  const group = (title: string, list: GoogleCalendar[]): JSX.Element | null => list.length === 0 ? null : (
    <>
      <h3>{title}</h3>
      {list.map((c) => <CalToggle key={c.id} c={c} on={shown(c)} onToggle={() => toggle(c)} />)}
    </>
  )
  return (
    <aside className="cal-cals" aria-label="Calendars">
      {!ready && <p className="muted small">Loading calendars…</p>}
      {ready && calendars.length === 0 && <p className="muted small">No calendars yet.</p>}
      {group('My calendars', mine)}
      {group('Other calendars', other)}
    </aside>
  )
}

export default function CalendarView(): JSX.Element {
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const google = useStore((s) => s.google)
  const todos = useStore((s) => s.todos)
  const { toggleSidebar, refreshTodos, openSettings, toast, newChat, send, updateTodo, setView } = useStore()
  const [week, setWeek] = useState(() => startOfWeek(new Date()))
  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<{ event: CalendarEvent | null; draft?: EventDraft } | null>(null)
  // Bumped when the calendar/color cache resolves, so the grid picks up event colors.
  const [, setMetaTick] = useState(0)
  const { calendars, visibleIds, query, ready, shown: calendarOn, toggle } = useVisibleCalendars()

  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(week, i)), [week])
  const cacheKey = google?.connected && query ? calendarViewKey(week.toISOString(), 7, query) : ''
  // Paint the saved week in this render, before the sync request returns, so stepping
  // back to a week already opened does not flash "Loading…".
  const [painted, setPainted] = useState(cacheKey)
  if (cacheKey !== painted) {
    setPainted(cacheKey)
    if (!query) { setEvents([]); setLoading(false) }
    else {
      const cached = readView<CalendarEvent[]>(cacheKey)
      setEvents(cached ?? [])
      setLoading(cached == null)
      setError(null)
    }
  }
  const shown = useMemo(() => {
    const base = withoutTodoEvents(events, todos)
    if (!ready || calendars.length === 0) return base
    const ids = new Set(visibleIds)
    return base.filter((e) => !e.calendar_id || ids.has(e.calendar_id))
  }, [events, todos, ready, calendars.length, visibleIds])

  // The week on screen is the saved copy until this returns. `refresh` is the Refresh
  // button: it still only asks Google for what changed, and the grid stays up meanwhile.
  // Every fetch takes a number and only the newest may paint, so a slow Refresh that lands after
  // "Next week" cannot put the previous week's events over the current one.
  const seq = useRef(0)
  const load = async (refresh = false): Promise<void> => {
    if (!google?.connected || query == null) return
    if (!query) { setEvents([]); return }
    const key = calendarViewKey(week.toISOString(), 7, query)
    const mine = ++seq.current
    if (refresh) setLoading(true)
    setError(null)
    try {
      const list = await api.google.calendarRange(week.toISOString(), 7, query, refresh)
      writeView(key, list)
      if (seq.current !== mine) return
      setEvents(list)
      setError(null)
    } catch (e) {
      if (seq.current === mine) setError((e as Error).message)
    } finally {
      if (seq.current === mine) setLoading(false)
    }
  }
  useEffect(() => {
    const mine = ++seq.current
    if (!google?.connected || query == null) return
    if (!query) return
    const key = calendarViewKey(week.toISOString(), 7, query)
    let alive = true
    api.google.calendarRange(week.toISOString(), 7, query)
      .then((list) => { writeView(key, list); if (alive && seq.current === mine) { setEvents(list); setError(null) } })
      .catch((e) => { if (alive && seq.current === mine) setError((e as Error).message) })
      .finally(() => { if (alive && seq.current === mine) setLoading(false) })
    return () => { alive = false }
  }, [week, google?.connected, query])
  useEffect(() => { void refreshTodos('all', false) }, [refreshTodos])
  useEffect(() => {
    if (google?.connected) void primeCalendarMeta().then(() => setMetaTick((t) => t + 1))
  }, [google?.connected])

  const create = async (slot: Slot, title: string): Promise<boolean> => {
    try {
      await api.google.createEvent({ summary: title, start: slotIso(slot.day, slot.startMin), end: slotIso(slot.day, slot.endMin) })
      toast(`Added "${title}"`)
      await load()
      return true
    } catch (e) {
      toast((e as Error).message, 'error')
      return false
    }
  }

  // Dragging a block only writes its start and end; every other field is left alone, and the grid
  // shows the new position straight away so it does not snap back while Google is answering.
  const move = async (e: CalendarEvent, start: string, end: string): Promise<void> => {
    // A new time for a meeting is news to its guests, so ask before mailing them, as Google does.
    const send_updates = e.attendees.length > 0 && window.confirm(`Email the ${e.attendees.length} guest${e.attendees.length > 1 ? 's' : ''} about the new time?`) ? 'all' : 'none'
    const before = events
    setEvents((list) => list.map((x) => (x.id === e.id ? { ...x, start: new Date(start).toISOString(), end: new Date(end).toISOString() } : x)))
    try {
      await api.google.updateEvent(e.id, { start, end, calendar_id: e.calendar_id ?? 'primary', send_updates })
      toast(`Moved "${e.summary || 'event'}" to ${new Date(start).toLocaleString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' })}${e.recurring_event_id ? ' (this occurrence)' : ''}`)
      await load()
    } catch (err) {
      setEvents(before)
      toast((err as Error).message, 'error')
    }
  }

  const dropTodo = async (todoId: string, day: string, hour: number | null): Promise<void> => {
    const todo = useStore.getState().todos.find((t) => t.id === todoId)
    if (!todo) return
    try {
      // scheduleTodo writes the due date with the event link, so the mirror never sees one without the other.
      if (!google?.connected) await updateTodo(todoId, { due: day })
      else {
        const start = hour === null ? day : `${day}T${String(hour).padStart(2, '0')}:00:00`
        await scheduleTodo(todo, start)
        if (hour !== null) await load()
      }
      toast(hour === null ? `Due ${day}` : `Scheduled ${hour}:00`)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const focus = editing?.event ?? null
  const fmtEvent = (e: CalendarEvent): string => {
    const when = e.all_day ? e.start : new Date(e.start).toLocaleString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' })
    const title = oneLine(e.summary || '') || '(no title)'
    const loc = e.location ? ` at ${oneLine(e.location, 80)}` : ''
    return `${when} — ${title} (\`${oneLine(e.id, 80)}\`)${loc}`
  }
  const span = `${days[0].toDateString()} – ${days[6].toDateString()}`
  usePageContext(() => ({
    view: 'calendar',
    label: focus ? `Event “${oneLine(focus.summary || '') || 'untitled'}”` : `Calendar · ${span}`,
    detail: [
      `The week of ${span} is on screen.`,
      focus ? `The user is editing this event: ${fmtEvent(focus)}${focus.description ? `\n\n${oneLine(focus.description, 400)}` : ''}` : '',
      events.length ? `Events that week:\n${lines(events, fmtEvent)}` : 'No events that week.',
      todos.some((t) => !t.done && t.due) ? `Todos with dates:\n${lines(todos.filter((t) => !t.done && t.due), (t) => `${t.due} — ${t.title} (\`${t.id}\`)`)}` : ''
    ].filter(Boolean).join('\n\n'),
    refs: (focus ? [{ kind: 'event', id: focus.id, name: focus.summary }] : events.slice(0, 40).map((e) => ({ kind: 'event', id: e.id, name: e.summary }))),
    hints: focus ? ['Move this an hour later', 'Draft a note to the guests'] : ['Where is my free time this week?', 'Schedule my overdue todos into the gaps']
  }), [events, focus, todos, span])

  return (
    <main className="page cal-page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" aria-label="Show sidebar" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><CalIcon size={16} /> Calendar <span className="muted">· {days[0].toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} – {days[6].toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span></h2>
        <div className="no-drag header-right">
          {google?.connected && <button className="ghost-btn" onClick={() => setEditing({ event: null, draft: {} })}><Plus size={13} /> New event</button>}
          <SendToSpace items={[{ kind: 'calendar' }]} />
          <button className="ghost-btn" onClick={() => setWeek(startOfWeek(new Date()))}>Today</button>
          {google?.connected && <button className="icon-btn" title="Refresh" onClick={() => void load(true)} disabled={loading}><RefreshCw size={15} className={loading ? 'spin' : ''} /></button>}
          <button className="icon-btn" aria-label="Previous week" onClick={() => setWeek(addDays(week, -7))}><ChevronLeft size={16} /></button>
          <button className="icon-btn" aria-label="Next week" onClick={() => setWeek(addDays(week, 7))}><ChevronRight size={16} /></button>
          <button className="primary-btn" onClick={() => { newChat(null); void send('Help me plan this week. Look at my calendar for the next 7 days and my open todos, then propose a schedule.') }}>Plan my week</button>
        </div>
        <AppSwitcher />
      </header>

      {!google?.connected && (
        <div className="notice-bar">Showing todos only. <button className="link" onClick={() => openSettings('integrations')}>Connect Google</button></div>
      )}
      {error && <div className="notice-bar error">{error}</div>}

      <div className="cal-body">
        {google?.connected && <CalendarRail calendars={calendars} ready={ready} shown={calendarOn} toggle={toggle} />}
        <div className="cal-scroll">
          <CalendarWeek days={days} events={shown} todos={todos} canCreate={!!google?.connected}
            onOpen={(e) => setEditing({ event: e })} onTodo={() => setView('todos')} onTodoDrop={(id, day, hour) => void dropTodo(id, day, hour)} onCreate={create}
            onCreateFull={(slot, title) => setEditing({ event: null, draft: { day: slot.day, title, start: slotIso(slot.day, slot.startMin), end: slotIso(slot.day, slot.endMin) } })}
            onCreateAllDay={(day) => setEditing({ event: null, draft: { day, allDay: true } })}
            onMove={google?.connected ? (e, start, end) => void move(e, start, end) : undefined}
            colorOf={eventColor} />
        </div>
      </div>
      {loading && events.length === 0 && <div className="cal-loading">Loading…</div>}

      {editing && (
        <EventEditor key={editing.event?.id ?? `new:${editing.draft?.day ?? ''}:${editing.draft?.hour ?? ''}:${editing.draft?.start ?? ''}:${editing.draft?.end ?? ''}:${editing.draft?.allDay ? 'day' : ''}`}
          event={editing.event} draft={editing.draft} onClose={() => setEditing(null)} onSaved={() => void load()} />
      )}
    </main>
  )
}
