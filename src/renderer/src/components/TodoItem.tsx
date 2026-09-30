import { useState } from 'react'
import { Check, Trash2, Calendar, CalendarPlus, ExternalLink } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import type { Todo } from '@shared/types'
import { dragProps } from '../canvas/dnd'
import ProjectChip from './ProjectChip'
import { localDay } from './CalendarWeek'

export const dueLabel = (due: string | null): { text: string; cls: string } => {
  if (!due) return { text: '', cls: '' }
  const d = new Date(due + 'T00:00:00')
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const diff = Math.round((d.getTime() - today.getTime()) / 86_400_000)
  if (diff < 0) return { text: `${-diff}d overdue`, cls: 'overdue' }
  if (diff === 0) return { text: 'Today', cls: 'today' }
  if (diff === 1) return { text: 'Tomorrow', cls: '' }
  if (diff < 7) return { text: d.toLocaleDateString(undefined, { weekday: 'short' }), cls: '' }
  return { text: d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }), cls: '' }
}

/** Put a todo on Google Calendar. `start` is YYYY-MM-DD (all-day) or a local datetime. */
export async function scheduleTodo(todo: Todo, start?: string): Promise<Todo> {
  const when = start || todo.due || localDay()
  const app = useStore.getState()
  if (!todo.due && when.length === 10) await app.updateTodo(todo.id, { due: when })
  else if (when.length === 10 && todo.due !== when) await app.updateTodo(todo.id, { due: when })
  const ev = await api.google.createEvent({
    summary: todo.title,
    start: when,
    description: todo.notes || undefined
  })
  await app.updateTodo(todo.id, { calendar_event_id: ev.id, calendar_link: ev.link })
  return { ...todo, due: when.length === 10 ? when : todo.due, calendar_event_id: ev.id, calendar_link: ev.link }
}

export default function TodoItem({ todo, showProject = true, compact = false }: { todo: Todo; showProject?: boolean; compact?: boolean }): JSX.Element {
  const updateTodo = useStore((s) => s.updateTodo)
  const deleteTodo = useStore((s) => s.deleteTodo)
  const toast = useStore((s) => s.toast)
  const google = useStore((s) => s.google)
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(todo.title)
  const due = dueLabel(todo.due)
  const commit = (): void => {
    setEditing(false)
    if (title.trim() && title !== todo.title) void updateTodo(todo.id, { title: title.trim() })
    else setTitle(todo.title)
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
    <div className={`todo ${todo.done ? 'done' : ''} p${todo.priority} ${compact ? 'compact' : ''}`} {...(editing ? {} : drag)}>
      <button className="todo-check" onClick={() => void updateTodo(todo.id, { done: !todo.done })} title={todo.done ? 'Reopen' : 'Complete'}>
        {todo.done ? <Check size={12} /> : null}
      </button>
      <div className="todo-main">
        {editing ? (
          <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { setTitle(todo.title); setEditing(false) } }} />
        ) : (
          <span className="todo-title" role="button" tabIndex={0} onClick={() => setEditing(true)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setEditing(true) } }}>{todo.title}</span>
        )}
        {!compact && todo.notes && <span className="todo-notes">{todo.notes}</span>}
      </div>
      <div className="todo-meta">
        {todo.external_id && <span className="g-logo g-logo-sm" title="Synced with Google Tasks">G</span>}
        {showProject && todo.project_id && <ProjectChip projectId={todo.project_id} />}
        <label className={`todo-due ${due.cls}`} title="Due date">
          <Calendar size={11} />
          <span>{due.text || 'no date'}</span>
          <input type="date" aria-label={`Due date for ${todo.title}`} value={todo.due ?? ''} onChange={(e) => void updateTodo(todo.id, e.target.value ? { due: e.target.value } : { clear_due: true })} />
        </label>
        {!compact && (
          <select className="todo-prio" value={todo.priority} onChange={(e) => void updateTodo(todo.id, { priority: Number(e.target.value) })} title="Priority">
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
