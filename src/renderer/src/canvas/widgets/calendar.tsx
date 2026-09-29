import { useEffect, useMemo, useState } from 'react'
import { Calendar as CalIcon, ChevronLeft, ChevronRight, ExternalLink } from 'lucide-react'
import type { CalendarEvent } from '@shared/types'
import { useStore } from '../../store'
import { api } from '../../lib/api'
import CalendarWeek, { addDays, dayKey, fmtTime, startOfWeek } from '../../components/CalendarWeek'
import type { WidgetDef, WidgetProps } from '../registry'

type Mode = 'agenda' | 'day' | 'week'

const MODES: Mode[] = ['agenda', 'day', 'week']
const DAY_CHOICES = [1, 3, 7, 14]
const POLL_MS = 120_000
const readMode = (v: unknown): Mode => (MODES.includes(v as Mode) ? (v as Mode) : 'agenda')

const CalendarWidget = ({ window: win, live, onConfig }: WidgetProps): JSX.Element => {
  const connected = useStore((s) => s.google?.connected ?? false)
  const todos = useStore((s) => s.todos)
  const toast = useStore((s) => s.toast)
  const setSettingsOpen = useStore((s) => s.setSettingsOpen)
  const mode = readMode(win.config.mode)
  const days = typeof win.config.days === 'number' ? win.config.days : 7
  const [shift, setShift] = useState(0)
  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<CalendarEvent | null>(null)

  const anchor = useMemo(() => (mode === 'week' ? addDays(startOfWeek(new Date()), shift * 7) : addDays(new Date(), shift)), [mode, shift])
  const span = mode === 'week' ? 7 : mode === 'day' ? 1 : days
  const columns = useMemo(() => (mode === 'agenda' ? [] : Array.from({ length: span }, (_, i) => addDays(anchor, i))), [mode, span, anchor])
  const startIso = anchor.toISOString()

  // The whole point of `live`: a window that is off-screen, minimized or zoomed out stops its poller,
  // and the interval is never created in the first place.
  useEffect(() => {
    if (!live || !connected) return
    let alive = true
    const pull = async (): Promise<void> => {
      try {
        const list = mode === 'agenda' ? await api.google.calendar(span) : await api.google.calendarRange(startIso, span)
        if (alive) { setEvents(list); setError(null) }
      } catch (e) {
        if (alive) setError((e as Error).message)
      }
    }
    void pull()
    const t = setInterval(() => void pull(), POLL_MS)
    return () => { alive = false; clearInterval(t) }
  }, [live, connected, mode, span, startIso])

  const create = async (day: string, hour: number, title: string): Promise<boolean> => {
    try {
      await api.google.createEvent({ summary: title, start: `${day}T${String(hour).padStart(2, '0')}:00:00` })
      toast(`Added "${title}"`)
      setEvents(mode === 'agenda' ? await api.google.calendar(span) : await api.google.calendarRange(startIso, span))
      return true
    } catch (e) {
      toast((e as Error).message, 'error')
      return false
    }
  }

  const agenda = useMemo(() => {
    if (mode !== 'agenda') return []
    const m = new Map<string, { events: CalendarEvent[]; todos: string[] }>()
    const slot = (k: string): { events: CalendarEvent[]; todos: string[] } => {
      const cur = m.get(k) ?? { events: [], todos: [] }
      m.set(k, cur)
      return cur
    }
    for (const e of events) slot(e.all_day ? e.start : dayKey(new Date(e.start))).events.push(e)
    const last = dayKey(addDays(new Date(), span - 1))
    for (const t of todos) if (t.due && !t.done && t.due <= last && t.due >= dayKey(new Date())) slot(t.due).todos.push(t.title)
    return [...m.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))
  }, [mode, events, todos, span])

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
    <div className="widget">
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
        {error && <span className="widget-meta" title={error}>offline</span>}
      </div>

      {!connected ? (
        <div className="widget-empty">
          <span>No calendar connected.</span>
          <button className="widget-chip" onClick={() => setSettingsOpen(true)}>Connect Google</button>
        </div>
      ) : mode === 'agenda' ? (
        <div className="widget-scroll">
          {agenda.length === 0 && <div className="widget-empty"><span>Nothing in the next {span} days.</span></div>}
          <div className="widget-list">
            {agenda.map(([k, g]) => (
              <div key={k}>
                <div className="widget-meta">{new Date(`${k}T00:00:00`).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })}</div>
                {g.events.map((e) => (
                  <button key={e.id} className="widget-row" onClick={() => setOpen(e)}>
                    <span className="widget-meta">{e.all_day ? 'all day' : fmtTime(new Date(e.start))}</span>
                    <span className="grow widget-title">{e.summary}</span>
                  </button>
                ))}
                {g.todos.map((t) => (
                  <div key={t} className="widget-row">
                    <span className="widget-meta">todo</span>
                    <span className="grow widget-sub">○ {t}</span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="cal-scroll">
          <CalendarWeek days={columns} events={events} todos={todos} canCreate onOpen={setOpen} onCreate={create} />
        </div>
      )}

      {open && (
        <div className="widget-bar">
          <span className="grow widget-sub" title={open.description || open.summary}>{open.summary} · {open.all_day ? 'all day' : fmtTime(new Date(open.start))}{open.location ? ` · ${open.location}` : ''}</span>
          {open.link && <a className="widget-chip" href={open.link} target="_blank" rel="noreferrer"><ExternalLink size={11} /></a>}
          <button className="widget-chip" onClick={() => setOpen(null)}>close</button>
        </div>
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
  Component: CalendarWidget
}

export default CalendarWidget
