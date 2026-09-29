import { useEffect, useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, PanelLeftOpen, Calendar as CalIcon, Plus, ExternalLink, X } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import type { CalendarEvent, Todo } from '@shared/types'

const HOUR_PX = 44
const startOfWeek = (d: Date): Date => { const x = new Date(d); x.setHours(0, 0, 0, 0); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x }
const addDays = (d: Date, n: number): Date => { const x = new Date(d); x.setDate(x.getDate() + n); return x }
const key = (d: Date): string => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const fmtT = (d: Date): string => d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })

export default function CalendarView(): JSX.Element {
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const google = useStore((s) => s.google)
  const todos = useStore((s) => s.todos)
  const { toggleSidebar, refreshTodos, setSettingsOpen, toast, newChat, send } = useStore()
  const [week, setWeek] = useState(() => startOfWeek(new Date()))
  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState<{ day: string; hour: number } | null>(null)
  const [title, setTitle] = useState('')
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

  const eventsByDay = useMemo(() => {
    const m: Record<string, CalendarEvent[]> = {}
    for (const e of events) {
      const d = e.all_day ? e.start : key(new Date(e.start))
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
    if (!creating || !title.trim()) return
    const start = `${creating.day}T${String(creating.hour).padStart(2, '0')}:00:00`
    try {
      await api.google.createEvent({ summary: title.trim(), start })
      toast(`Added "${title.trim()}"`)
      setTitle(''); setCreating(null); await load()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const todayKey = key(new Date())
  const nowTop = (new Date().getHours() + new Date().getMinutes() / 60) * HOUR_PX

  return (
    <main className="page cal-page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><CalIcon size={16} /> Calendar <span className="muted">· {days[0].toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} – {days[6].toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span></h2>
        <div className="no-drag header-right">
          <button className="ghost-btn" onClick={() => setWeek(startOfWeek(new Date()))}>Today</button>
          <button className="icon-btn" onClick={() => setWeek(addDays(week, -7))}><ChevronLeft size={16} /></button>
          <button className="icon-btn" onClick={() => setWeek(addDays(week, 7))}><ChevronRight size={16} /></button>
          <button className="primary-btn" onClick={() => { newChat(null); void send('Help me plan this week. Look at my calendar for the next 7 days and my open todos, then propose a schedule.') }}>Plan my week</button>
        </div>
      </header>

      {!google?.connected && (
        <div className="notice-bar">Showing todos only. <button className="link" onClick={() => setSettingsOpen(true)}>Connect Google</button> to see and create calendar events.</div>
      )}
      {error && <div className="notice-bar error">{error}</div>}

      <div className="cal-scroll">
        <div className="cal-grid">
          <div className="cal-corner" />
          {days.map((d) => (
            <div key={key(d)} className={`cal-dayhead ${key(d) === todayKey ? 'today' : ''}`}>
              <span className="dow">{d.toLocaleDateString(undefined, { weekday: 'short' })}</span>
              <span className="dom">{d.getDate()}</span>
            </div>
          ))}
          <div className="cal-allday-label">all day</div>
          {days.map((d) => (
            <div key={'ad' + key(d)} className="cal-allday">
              {(eventsByDay[key(d)] ?? []).filter((e) => e.all_day).map((e) => <div key={e.id} className="cal-chip" onClick={() => setOpen(e)}>{e.summary}</div>)}
              {(todosByDay[key(d)] ?? []).map((t) => <div key={t.id} className={`cal-chip todo p${t.priority}`} title="Todo due">○ {t.title}</div>)}
            </div>
          ))}
          <div className="cal-hours">
            {Array.from({ length: 24 }, (_, h) => <div key={h} className="cal-hour" style={{ height: HOUR_PX }}>{h === 0 ? '' : `${h % 12 || 12}${h < 12 ? 'am' : 'pm'}`}</div>)}
          </div>
          {days.map((d) => {
            const dk = key(d)
            return (
              <div key={'col' + dk} className={`cal-col ${dk === todayKey ? 'today' : ''}`} style={{ height: 24 * HOUR_PX }}
                onDoubleClick={(e) => { if (!google?.connected) return; const rect = e.currentTarget.getBoundingClientRect(); setCreating({ day: dk, hour: Math.floor((e.clientY - rect.top) / HOUR_PX) }) }}>
                {Array.from({ length: 24 }, (_, h) => <div key={h} className="cal-line" style={{ top: h * HOUR_PX }} />)}
                {dk === todayKey && <div className="cal-now" style={{ top: nowTop }} />}
                {(eventsByDay[dk] ?? []).filter((e) => !e.all_day).map((e) => {
                  const s = new Date(e.start), en = new Date(e.end)
                  const top = (s.getHours() + s.getMinutes() / 60) * HOUR_PX
                  const h = Math.max(22, ((en.getTime() - s.getTime()) / 3_600_000) * HOUR_PX - 2)
                  return (
                    <div key={e.id} className="cal-event" style={{ top, height: h }} onClick={() => setOpen(e)} title={e.summary}>
                      <b>{e.summary}</b><span>{fmtT(s)}</span>
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
      </div>
      {loading && <div className="cal-loading">Loading…</div>}
      {google?.connected && <p className="composer-hint">Double-click a time slot to add an event.</p>}

      {open && (
        <div className="modal-backdrop" onMouseDown={() => setOpen(null)}>
          <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
            <header><h2>{open.summary}</h2><button className="icon-btn" onClick={() => setOpen(null)}><X size={16} /></button></header>
            <section>
              <p>{open.all_day ? 'All day' : `${new Date(open.start).toLocaleString()} – ${fmtT(new Date(open.end))}`}</p>
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
