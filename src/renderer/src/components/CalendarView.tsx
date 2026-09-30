import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, PanelLeftOpen, Calendar as CalIcon, ExternalLink, Layers, Video, X } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import CalendarWeek, { addDays, fmtTime, startOfWeek } from './CalendarWeek'
import CalendarMonth, { monthGridStart } from './CalendarMonth'
import type { CalendarEvent, GoogleCalendar } from '@shared/types'

const VIEWS = ['day', 'week', 'month'] as const
type CalView = (typeof VIEWS)[number]

const storedView = (): CalView => {
  try {
    const v = localStorage.getItem('calendar.view')
    return VIEWS.includes(v as CalView) ? (v as CalView) : 'week'
  } catch {
    return 'week'
  }
}
// null means "no local override yet": mirror which calendars are checked in Google's own UI.
const storedShown = (): Set<string> | null => {
  try {
    const raw = localStorage.getItem('calendar.shown')
    return raw ? new Set(JSON.parse(raw) as string[]) : null
  } catch {
    return null
  }
}

export default function CalendarView(): JSX.Element {
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const google = useStore((s) => s.google)
  const todos = useStore((s) => s.todos)
  const toggleSidebar = useStore((s) => s.toggleSidebar)
  const refreshTodos = useStore((s) => s.refreshTodos)
  const setSettingsOpen = useStore((s) => s.setSettingsOpen)
  const toast = useStore((s) => s.toast)
  const newChat = useStore((s) => s.newChat)
  const send = useStore((s) => s.send)

  const [view, setViewState] = useState<CalView>(storedView)
  const [anchor, setAnchor] = useState(() => new Date())
  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [cals, setCals] = useState<GoogleCalendar[]>([])
  const [override, setOverride] = useState<Set<string> | null>(storedShown)
  const [railOpen, setRailOpen] = useState(true)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<CalendarEvent | null>(null)
  const [reload, setReload] = useState(0)
  const seq = useRef(0)

  const setView = (v: CalView): void => {
    setViewState(v)
    try { localStorage.setItem('calendar.view', v) } catch { /* private-mode etc: view just won't persist */ }
  }

  const shown = useMemo(
    () => override ?? new Set(cals.filter((c) => c.selected).map((c) => c.id)),
    [override, cals]
  )

  const range = useMemo(() => {
    if (view === 'day') { const d = new Date(anchor); d.setHours(0, 0, 0, 0); return { start: d, span: 1 } }
    if (view === 'week') return { start: startOfWeek(anchor), span: 7 }
    return { start: monthGridStart(anchor), span: 42 }
  }, [view, anchor])

  const days = useMemo(
    () => (view === 'day' ? [range.start] : Array.from({ length: 7 }, (_, i) => addDays(startOfWeek(anchor), i))),
    [view, range, anchor]
  )

  useEffect(() => {
    if (!google?.connected) { setCals([]); return }
    let alive = true
    api.google.calendars().then((l) => { if (alive) setCals(l) }).catch(() => undefined)
    return () => { alive = false }
  }, [google?.connected])

  useEffect(() => {
    if (!google?.connected) { setEvents([]); return }
    const id = ++seq.current
    setLoading(true); setError(null)
    // The default set is one merged fetch; a customized set fans out per calendar, so even
    // calendars unchecked in Google's UI (which the merged fetch skips) can be shown here.
    const selected = cals.filter((c) => c.selected).map((c) => c.id)
    const isDefault = cals.length === 0 || (shown.size === selected.length && selected.every((cid) => shown.has(cid)))
    const startIso = range.start.toISOString()
    const pull = async (): Promise<void> => {
      try {
        const lists = isDefault
          ? [await api.google.calendarRange(startIso, range.span, undefined, 250)]
          : await Promise.all([...shown].map((cid) => api.google.calendarRange(startIso, range.span, cid, 250)))
        if (id !== seq.current) return
        setEvents(lists.flat().sort((a, b) => ((a.start || '') < (b.start || '') ? -1 : 1)))
      } catch (e) {
        if (id === seq.current) setError((e as Error).message)
      } finally {
        if (id === seq.current) setLoading(false)
      }
    }
    void pull()
  }, [google?.connected, range, cals, shown, reload])

  useEffect(() => { void refreshTodos('all', false) }, [refreshTodos])

  const toggleCal = (cid: string): void => {
    const next = new Set(shown)
    if (next.has(cid)) next.delete(cid); else next.add(cid)
    setOverride(next)
    try { localStorage.setItem('calendar.shown', JSON.stringify([...next])) } catch { /* fine */ }
  }

  const shiftAnchor = (dir: number): void => {
    setAnchor(view === 'day' ? addDays(anchor, dir)
      : view === 'week' ? addDays(anchor, dir * 7)
        : new Date(anchor.getFullYear(), anchor.getMonth() + dir, 1))
  }

  const create = async (day: string, hour: number, title: string): Promise<boolean> => {
    try {
      await api.google.createEvent({ summary: title, start: `${day}T${String(hour).padStart(2, '0')}:00:00` })
      toast(`Added "${title}"`)
      setReload((n) => n + 1)
      return true
    } catch (e) {
      toast((e as Error).message, 'error')
      return false
    }
  }

  const title = view === 'month'
    ? anchor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
    : view === 'day'
      ? anchor.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })
      : `${days[0].toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} – ${days[6].toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`

  return (
    <main className="page cal-page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><CalIcon size={16} /> Calendar <span className="muted">· {title}</span></h2>
        <div className="no-drag header-right">
          <div className="seg">
            {VIEWS.map((v) => <button key={v} className={v === view ? 'active' : ''} onClick={() => setView(v)}>{v[0].toUpperCase() + v.slice(1)}</button>)}
          </div>
          {google?.connected && (
            <button className={`icon-btn ${railOpen ? 'on' : ''}`} title="Show or hide calendars" onClick={() => setRailOpen(!railOpen)}><Layers size={16} /></button>
          )}
          <button className="ghost-btn" onClick={() => setAnchor(new Date())}>Today</button>
          <button className="icon-btn" onClick={() => shiftAnchor(-1)}><ChevronLeft size={16} /></button>
          <button className="icon-btn" onClick={() => shiftAnchor(1)}><ChevronRight size={16} /></button>
          <button className="primary-btn" onClick={() => { newChat(null); void send('Help me plan this week. Look at my calendar for the next 7 days and my open todos, then propose a schedule.') }}>Plan my week</button>
        </div>
      </header>

      {!google?.connected && (
        <div className="notice-bar">Showing todos only. <button className="link" onClick={() => setSettingsOpen(true)}>Connect Google</button></div>
      )}
      {error && <div className="notice-bar error">{error}</div>}

      <div className="cal-body">
        {google?.connected && railOpen && (
          <aside className="cal-rail">
            <h4>My calendars</h4>
            {cals.map((c) => (
              <label key={c.id} className={`cal-rail-item ${shown.has(c.id) ? '' : 'off'}`} title={c.name}>
                <input type="checkbox" checked={shown.has(c.id)} style={c.color ? { accentColor: c.color } : undefined} onChange={() => toggleCal(c.id)} />
                <span className="name">{c.name}</span>
              </label>
            ))}
            {cals.length === 0 && <span className="muted small">No calendars found.</span>}
          </aside>
        )}
        {view === 'month' ? (
          <CalendarMonth month={anchor} events={events} todos={todos} onOpen={setOpen} onPickDay={(d) => { setAnchor(d); setView('day') }} />
        ) : (
          <div className="cal-scroll">
            <CalendarWeek days={days} events={events} todos={todos} canCreate={!!google?.connected} onOpen={setOpen} onCreate={create} />
          </div>
        )}
      </div>
      {loading && <div className="cal-loading">Loading…</div>}

      {open && (
        <div className="modal-backdrop" onMouseDown={() => setOpen(null)}>
          <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
            <header><h2>{open.summary}</h2><button className="icon-btn" onClick={() => setOpen(null)}><X size={16} /></button></header>
            <section>
              <p>{open.all_day ? 'All day' : `${new Date(open.start).toLocaleString()} – ${fmtTime(new Date(open.end))}`}</p>
              {open.calendar && <p className="muted small"><span className="cal-dot" style={open.color ? { background: open.color } : undefined} /> {open.calendar}</p>}
              {open.location && <p className="muted">{open.location}</p>}
              {open.attendees.length > 0 && <p className="muted small">With {open.attendees.join(', ')}</p>}
              {open.description && <p className="muted small" style={{ whiteSpace: 'pre-wrap' }}>{open.description}</p>}
            </section>
            <footer>
              {open.meet && <a className="ghost-btn" href={open.meet} target="_blank" rel="noreferrer"><Video size={13} /> Join Meet</a>}
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
