import { useEffect, useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, PanelLeftOpen, Calendar as CalIcon, ExternalLink, X } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import CalendarWeek, { addDays, fmtTime, startOfWeek } from './CalendarWeek'
import { scheduleTodo } from './TodoItem'
import type { CalendarEvent } from '@shared/types'

export default function CalendarView(): JSX.Element {
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const google = useStore((s) => s.google)
  const todos = useStore((s) => s.todos)
  const { toggleSidebar, refreshTodos, setSettingsOpen, toast, newChat, send, updateTodo, setView } = useStore()
  const [week, setWeek] = useState(() => startOfWeek(new Date()))
  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<CalendarEvent | null>(null)

  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(week, i)), [week])

  const load = async (): Promise<void> => {
    if (!google?.connected) return
    setLoading(true); setError(null)
    try {
      setEvents(await api.google.calendarRange(week.toISOString(), 7))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { void load() }, [week, google?.connected])
  useEffect(() => { void refreshTodos('all', false) }, [refreshTodos])

  const create = async (day: string, hour: number, title: string): Promise<boolean> => {
    const start = `${day}T${String(hour).padStart(2, '0')}:00:00`
    try {
      await api.google.createEvent({ summary: title, start })
      toast(`Added "${title}"`)
      await load()
      return true
    } catch (e) {
      toast((e as Error).message, 'error')
      return false
    }
  }

  const dropTodo = async (todoId: string, day: string, hour: number | null): Promise<void> => {
    const todo = useStore.getState().todos.find((t) => t.id === todoId)
    if (!todo) return
    try {
      await updateTodo(todoId, { due: day })
      if (google?.connected) {
        const start = hour === null ? day : `${day}T${String(hour).padStart(2, '0')}:00:00`
        await scheduleTodo({ ...todo, due: day }, start)
        if (hour !== null) await load()
      }
      toast(hour === null ? `Due ${day}` : `Scheduled ${hour}:00`)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  return (
    <main className="page cal-page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" aria-label="Show sidebar" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><CalIcon size={16} /> Calendar <span className="muted">· {days[0].toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} – {days[6].toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span></h2>
        <div className="no-drag header-right">
          <button className="ghost-btn" onClick={() => setWeek(startOfWeek(new Date()))}>Today</button>
          <button className="icon-btn" aria-label="Previous week" onClick={() => setWeek(addDays(week, -7))}><ChevronLeft size={16} /></button>
          <button className="icon-btn" aria-label="Next week" onClick={() => setWeek(addDays(week, 7))}><ChevronRight size={16} /></button>
          <button className="primary-btn" onClick={() => { newChat(null); void send('Help me plan this week. Look at my calendar for the next 7 days and my open todos, then propose a schedule.') }}>Plan my week</button>
        </div>
      </header>

      {!google?.connected && (
        <div className="notice-bar">Showing todos only. <button className="link" onClick={() => setSettingsOpen(true)}>Connect Google</button></div>
      )}
      {error && <div className="notice-bar error">{error}</div>}

      <div className="cal-scroll">
        <CalendarWeek days={days} events={events} todos={todos} canCreate={!!google?.connected}
          onOpen={setOpen} onTodo={() => setView('todos')} onTodoDrop={(id, day, hour) => void dropTodo(id, day, hour)} onCreate={create} />
      </div>
      {loading && <div className="cal-loading">Loading…</div>}

      {open && (
        <div className="modal-backdrop" onMouseDown={() => setOpen(null)}>
          <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
            <header><h2>{open.summary}</h2><button className="icon-btn" aria-label="Close event details" onClick={() => setOpen(null)}><X size={16} /></button></header>
            <section>
              <p>{open.all_day ? 'All day' : `${new Date(open.start).toLocaleString()} – ${fmtTime(new Date(open.end))}`}</p>
              {open.location && <p className="muted">{open.location}</p>}
              {open.attendees.length > 0 && <p className="muted small">With {open.attendees.join(', ')}</p>}
              {open.description && <p className="muted small" style={{ whiteSpace: 'pre-wrap' }}>{open.description}</p>}
            </section>
            <footer>
              {open.link && <a className="ghost-btn" href={open.link} target="_blank" rel="noreferrer"><ExternalLink size={13} /> Open in Google Calendar</a>}
              <span style={{ flex: 1 }} />
              <button className="primary-btn" onClick={() => { setOpen(null); newChat(null); void send(`Prep me for "${open.summary}" (${new Date(open.start).toLocaleString()}). Check my memory, documents and recent email for context on the attendees and topic, then give me a one-page brief.`) }}>Prep me</button>
            </footer>
          </div>
        </div>
      )}
    </main>
  )
}
