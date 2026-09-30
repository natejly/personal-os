import { useEffect, useMemo, useState, type DragEvent } from 'react'
import { Calendar as CalIcon, ChevronLeft, ChevronRight, ExternalLink, Pencil, Plus } from 'lucide-react'
import type { CalendarEvent, DragKind, Todo } from '@shared/types'
import { useStore } from '../../store'
import { api } from '../../lib/api'
import CalendarWeek, { addDays, dayKey, fmtTime, localDay, startOfWeek } from '../../components/CalendarWeek'
import EventEditor, { eventColor, primeCalendarMeta, type EventDraft } from '../../components/EventEditor'
import { scheduleTodo } from '../../components/TodoItem'
import { hasDrag, readDrag, useDropTarget } from '../dnd'
import type { WidgetDef, WidgetProps } from '../registry'

type CalMode = 'agenda' | 'day' | 'week'

const MODES: CalMode[] = ['agenda', 'day', 'week']
const DAY_CHOICES = [1, 3, 7, 14]
const POLL_MS = 120_000
const ACCEPTS: DragKind[] = ['todo']
const readMode = (v: unknown): Mode => (MODES.includes(v as Mode) ? (v as Mode) : 'agenda')

const CalendarWidget = ({ window: win, live, onConfig }: WidgetProps): JSX.Element => {
  const connected = useStore((s) => s.google?.connected ?? false)
  const todos = useStore((s) => s.todos)
  const toast = useStore((s) => s.toast)
  const setSettingsOpen = useStore((s) => s.setSettingsOpen)
  const setView = useStore((s) => s.setView)
  const updateTodo = useStore((s) => s.updateTodo)
  const mode = readMode(win.config.mode)
  const days = typeof win.config.days === 'number' ? win.config.days : 7
  const [shift, setShift] = useState(0)
  const [events, setEvents] = useState<CalendarEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<CalendarEvent | null>(null)
  const [editing, setEditing] = useState<{ event: CalendarEvent | null; draft?: EventDraft } | null>(null)
  const [, setMetaTick] = useState(0)

  const anchor = useMemo(() => (mode === 'week' ? addDays(startOfWeek(new Date()), shift * 7) : addDays(new Date(), shift)), [mode, shift])
  const span = mode === 'week' ? 7 : mode === 'day' ? 1 : days
  const columns = useMemo(() => (mode === 'agenda' ? [] : Array.from({ length: span }, (_, i) => addDays(anchor, i))), [mode, span, anchor])
  const startIso = anchor.toISOString()

  useEffect(() => {
    if (!live) return
    void useStore.getState().refreshTodos('all', false)
  }, [live])

  // Off-screen, minimized or zoomed out: stop the poller so a hidden window does not hit Google.
  useEffect(() => {
    if (!live || !connected) return
    let alive = true
    const pull = async (): Promise<void> => {
      try {
        const list = mode === 'agenda' ? await api.google.calendar(span, 'all') : await api.google.calendarRange(startIso, span, 'all')
        if (alive) { setEvents(list); setError(null) }
      } catch (e) {
        if (alive) setError((e as Error).message)
      }
    }
    void pull()
    void primeCalendarMeta().then(() => { if (alive) setMetaTick((t) => t + 1) })
    const t = setInterval(() => void pull(), POLL_MS)
    return () => { alive = false; clearInterval(t) }
  }, [live, connected, mode, span, startIso])

  const reload = async (): Promise<void> => {
    setEvents(mode === 'agenda' ? await api.google.calendar(span, 'all') : await api.google.calendarRange(startIso, span, 'all'))
  }

  const dropTodo = async (todoId: string, day: string, hour: number | null): Promise<void> => {
    const todo = useStore.getState().todos.find((t) => t.id === todoId)
    if (!todo) return
    try {
      await updateTodo(todoId, { due: day })
      if (connected) {
        const start = hour === null ? day : `${day}T${String(hour).padStart(2, '0')}:00:00`
        await scheduleTodo({ ...todo, due: day }, start)
        if (hour !== null) await reload()
      }
      toast(hour === null ? `Due ${day}` : `Scheduled ${hour}:00`)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const create = async (day: string, hour: number, title: string): Promise<boolean> => {
    try {
      await api.google.createEvent({ summary: title, start: `${day}T${String(hour).padStart(2, '0')}:00:00` })
      toast(`Added "${title}"`)
      await reload()
      return true
    } catch (e) {
      toast((e as Error).message, 'error')
      return false
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

  const agenda = useMemo(() => {
    if (mode !== 'agenda') return []
    const m = new Map<string, { events: CalendarEvent[]; todos: Todo[] }>()
    const slot = (k: string): { events: CalendarEvent[]; todos: Todo[] } => {
      const cur = m.get(k) ?? { events: [], todos: [] }
      m.set(k, cur)
      return cur
    }
    for (const e of events) slot(e.all_day ? e.start : dayKey(new Date(e.start))).events.push(e)
    const last = dayKey(addDays(new Date(), span - 1))
    for (const t of todos) if (t.due && !t.done && t.due <= last && t.due >= localDay()) slot(t.due).todos.push(t)
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
        {!connected && <button className="widget-chip" onClick={() => setSettingsOpen(true)}>Connect Google</button>}
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
                  <button key={e.id} className="widget-row" onClick={() => setOpen(e)}>
                    <span className="widget-meta">{e.all_day ? 'all day' : fmtTime(new Date(e.start))}</span>
                    <span className="grow widget-title">{e.summary}</span>
                  </button>
                ))}
                {g.todos.map((t) => (
                  <button key={t.id} className="widget-row" onClick={() => setView('todos')}>
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
          <CalendarWeek days={columns} events={events} todos={todos} canCreate={connected}
            onOpen={setOpen} onTodo={() => setView('todos')} onTodoDrop={(id, day, hour) => void dropTodo(id, day, hour)} onCreate={create}
            onCreateFull={(day, hour, title) => setEditing({ event: null, draft: { day, hour, title } })}
            colorOf={eventColor} />
        </div>
      )}

      {open && (
        <div className="widget-bar">
          <span className="grow widget-sub" title={open.description || open.summary}>{open.summary} · {open.all_day ? 'all day' : fmtTime(new Date(open.start))}{open.location ? ` · ${open.location}` : ''}</span>
          <button className="widget-chip" title="Edit event" onClick={() => { setEditing({ event: open }); setOpen(null) }}><Pencil size={11} /></button>
          {open.link && <a className="widget-chip" href={open.link} target="_blank" rel="noreferrer"><ExternalLink size={11} /></a>}
          <button className="widget-chip" onClick={() => setOpen(null)}>close</button>
        </div>
      )}

      {editing && (
        <EventEditor event={editing.event} draft={editing.draft} onClose={() => setEditing(null)} onSaved={() => void reload().catch(() => undefined)} />
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

export default CalendarWidget
