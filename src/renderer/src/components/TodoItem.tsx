import { memo, useRef, useState } from 'react'
import { Check, Trash2, Calendar, CalendarPlus, ExternalLink, Repeat, ListPlus, Lock } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import type { CalendarEvent, Todo } from '@shared/types'
import { dragProps } from '../canvas/dnd'
import ProjectChip from './ProjectChip'
import { rowButton } from '../lib/rowButton'
import { localDay } from './CalendarWeek'
import { dueLabel } from '../lib/dates'

export { dueLabel }

/** Put a todo on Google Calendar. `start` is YYYY-MM-DD (all-day) or a local datetime.
 *
 * A todo that already has an event (the mirror's, or an earlier placement) has that event moved;
 * a second event would leave the first one orphaned on the calendar. Otherwise the event is made
 * first and the due date and link are written together, so the todo -> calendar mirror
 * (todocal.py) is never poked with a dated, unlinked todo and never makes an event of its own.
 * Writing the link resets the mirror's signature, so its next pass adopts the event at the time
 * picked here.
 */
export async function scheduleTodo(todo: Todo, start?: string): Promise<Todo> {
  const app = useStore.getState()
  const cur = app.todos.find((t) => t.id === todo.id) ?? todo
  const when = start || todo.due || localDay()
  const mirror = app.todoCalendar
  const calendarId = (mirror?.config.enabled && mirror.config.calendarId) || undefined
  let ev: CalendarEvent | null = null
  if (cur.calendar_event_id) {
    // Gone or not writable: fall through and make a new one; the server tombstones the old link.
    // The mirror's all-day marker is free time; a block placed at an hour should look booked, like a new event.
    const busy = when.length > 10 ? { transparency: 'opaque' } : {}
    try { ev = await api.google.updateEvent(cur.calendar_event_id, { start: when, calendar_id: cur.calendar_id ?? 'primary', ...busy }) } catch { ev = null }
  }
  ev ??= await api.google.createEvent({
    summary: todo.title,
    start: when,
    description: todo.notes || undefined,
    ...(calendarId ? { calendar_id: calendarId } : {})
  })
  const link = { calendar_event_id: ev.id, calendar_link: ev.link, calendar_id: ev.calendar_id ?? calendarId ?? null }
  const due = when.slice(0, 10)
  await app.updateTodo(todo.id, { ...link, ...(cur.due !== due ? { due } : {}) })
  return { ...cur, due, ...link }
}

function TodoItem({ todo, showProject = true, compact = false, depth = 0, onTag }: { todo: Todo; showProject?: boolean; compact?: boolean; depth?: number; onTag?: (tag: string) => void }): JSX.Element {
  const updateTodo = useStore((s) => s.updateTodo)
  const deleteTodo = useStore((s) => s.deleteTodo)
  const toast = useStore((s) => s.toast)
  const google = useStore((s) => s.google)
  const addTodo = useStore((s) => s.addTodo)
  const [editing, setEditing] = useState(false)
  const [sub, setSub] = useState<string | null>(null)
  const [title, setTitle] = useState(todo.title)
  // The title as it was when editing began: a rename that lands meanwhile (sync, the agent) is not
  // something this edit changed, so it is neither overwritten nor reverted by a no-op commit.
  const startTitle = useRef(todo.title)
  const beginEdit = (): void => { startTitle.current = todo.title; setTitle(todo.title); setEditing(true) }
  const due = dueLabel(todo.due)
  const commit = (): void => {
    setEditing(false)
    if (title.trim() && title !== startTitle.current) void updateTodo(todo.id, { title: title.trim() })
  }
  const addSub = async (): Promise<void> => {
    const t = (sub ?? '').trim()
    setSub(null)
    if (!t) return
    try { await addTodo({ title: t, project_id: todo.project_id, parent_id: todo.id }) } catch (e) { toast((e as Error).message, 'error') }
  }
  const toCalendar = async (): Promise<void> => {
    try {
      await scheduleTodo(todo)
      toast(`“${todo.title}” added to calendar`)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  // A drag source for every canvas drop target that takes a todo; while the title is being edited the
  // attribute would eat the caret, so it comes off.
  const drag = dragProps({ kind: 'todo', id: todo.id, label: todo.title, projectId: todo.project_id })
  return (
    <div className={`todo ${todo.done ? 'done' : ''} p${todo.priority} ${compact ? 'compact' : ''}`} style={depth ? { marginLeft: depth * 20 } : undefined} {...(editing ? {} : drag)}>
      <button className="todo-check" onClick={() => void updateTodo(todo.id, { done: !todo.done })} title={todo.done ? 'Reopen' : 'Complete'} aria-label={`${todo.done ? 'Reopen' : 'Complete'}: ${todo.title}`}>
        {todo.done ? <Check size={12} /> : null}
      </button>
      <div className="todo-main">
        {editing ? (
          <input autoFocus aria-label="Todo title" value={title} onChange={(e) => setTitle(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { setTitle(startTitle.current); setEditing(false) } }} />
        ) : (
          <span className="todo-title" title="Click to rename" {...rowButton(beginEdit)}>{todo.title}</span>
        )}
        {!compact && todo.notes && <span className="todo-notes">{todo.notes}</span>}
        {sub !== null && (
          <input autoFocus className="todo-subinput" placeholder="Subtask…" aria-label={`New subtask of ${todo.title}`} value={sub} onChange={(e) => setSub(e.target.value)}
            onBlur={() => void addSub()} onKeyDown={(e) => { if (e.key === 'Enter') void addSub(); if (e.key === 'Escape') setSub(null) }} />
        )}
      </div>
      <div className="todo-meta">
        {todo.urgency !== undefined && !todo.done && <span className="todo-urgency" title="Urgency score (due, priority, age)">{todo.urgency.toFixed(1)}</span>}
        {!!todo.blocked_count && !todo.done && <span className="todo-blocked" title={`Waiting on ${todo.blocked_count} open todo${todo.blocked_count > 1 ? 's' : ''}`}><Lock size={11} /></span>}
        {todo.tags?.map((t) => <button key={t} className="todo-tag" title={`Filter by #${t}`} onClick={() => onTag?.(t)}>#{t}</button>)}
        {todo.repeat && <span className="todo-repeat" title={`Repeats every ${todo.repeat.every > 1 ? todo.repeat.every + ' ' : ''}${todo.repeat.unit}${todo.repeat.every > 1 ? 's' : ''}${todo.repeat.mode === 'from_completion' ? ' after completion' : ''}`}><Repeat size={11} /></span>}
        {todo.external_id && <span className="g-logo g-logo-sm" title="Synced with Google Tasks">G</span>}
        {showProject && todo.project_id && <ProjectChip projectId={todo.project_id} />}
        {/* `quiet`: a control with nothing set fades out until the row is hovered or focused. Its
            space is kept, so nothing moves when it comes back. */}
        <label className={`todo-due ${due.cls} ${todo.due ? '' : 'quiet'}`} title="Due date">
          <Calendar size={11} />
          <span>{due.text || 'no date'}</span>
          <input type="date" aria-label={`Due date for ${todo.title}`} value={todo.due ?? ''} onChange={(e) => void updateTodo(todo.id, e.target.value ? { due: e.target.value } : { clear_due: true })} />
        </label>
        {!compact && (
          <input className="todo-tags" placeholder="tags" title="Tags, comma separated" aria-label={`Tags for ${todo.title}`}
            defaultValue={(todo.tags ?? []).join(', ')} key={(todo.tags ?? []).join(',')}
            onBlur={(e) => { const v = e.target.value.split(',').map((x) => x.trim().replace(/^#/, '').toLowerCase()).filter(Boolean); if (v.join(',') !== (todo.tags ?? []).join(',')) void updateTodo(todo.id, { tags: v }) }} />
        )}
        {!compact && !todo.done && <button className="icon-btn ghost" title="Add subtask" aria-label={`Add subtask to ${todo.title}`} onClick={() => setSub('')}><ListPlus size={13} /></button>}
        {!compact && !todo.done && (
          <input className={`todo-est ${todo.estimate_min ? '' : 'quiet'}`} type="number" min={0} max={960} step={5} placeholder="min" title="Estimate in minutes (used by Plan my day)" aria-label={`Estimate in minutes for ${todo.title}`}
            defaultValue={todo.estimate_min ?? ''} key={todo.estimate_min ?? 'none'}
            onBlur={(e) => { const v = Number(e.target.value); if ((v || null) !== (todo.estimate_min ?? null)) void updateTodo(todo.id, v > 0 ? { estimate_min: v } : { clear_estimate: true }) }} />
        )}
        {!compact && (
          <select className={`todo-prio ${todo.priority === 2 ? 'quiet' : ''}`} value={todo.priority} onChange={(e) => void updateTodo(todo.id, { priority: Number(e.target.value) })} title="Priority" aria-label={`Priority of ${todo.title}`}>
            <option value={1}>P1</option><option value={2}>P2</option><option value={3}>P3</option>
          </select>
        )}
        {google?.connected && !todo.calendar_event_id && !todo.done && (
          <button className="icon-btn ghost" title="Add to Google Calendar" aria-label={`Add ${todo.title} to calendar`} onClick={() => void toCalendar()}>
            <CalendarPlus size={13} />
          </button>
        )}
        {todo.calendar_link && (
          <a className="icon-btn ghost" href={todo.calendar_link} target="_blank" rel="noreferrer" title="Open in Google Calendar" aria-label={`Open ${todo.title} in Google Calendar`}>
            <ExternalLink size={13} />
          </a>
        )}
        <button className="icon-btn ghost danger" aria-label={`Delete todo: ${todo.title}`} onClick={() => void deleteTodo(todo.id)}><Trash2 size={13} /></button>
      </div>
    </div>
  )
}

export default memo(TodoItem)
