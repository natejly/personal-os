import { useState } from 'react'
import { Check, Trash2, Calendar } from 'lucide-react'
import { useStore } from '../store'
import type { Todo } from '@shared/types'
import ProjectChip from './ProjectChip'

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

export default function TodoItem({ todo, showProject = true, compact = false }: { todo: Todo; showProject?: boolean; compact?: boolean }): JSX.Element {
  const { updateTodo, deleteTodo } = useStore()
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(todo.title)
  const due = dueLabel(todo.due)
  const commit = (): void => {
    setEditing(false)
    if (title.trim() && title !== todo.title) void updateTodo(todo.id, { title: title.trim() })
    else setTitle(todo.title)
  }
  return (
    <div className={`todo ${todo.done ? 'done' : ''} p${todo.priority} ${compact ? 'compact' : ''}`}>
      <button className="todo-check" onClick={() => void updateTodo(todo.id, { done: !todo.done })} title={todo.done ? 'Reopen' : 'Complete'}>
        {todo.done ? <Check size={12} /> : null}
      </button>
      <div className="todo-main">
        {editing ? (
          <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { setTitle(todo.title); setEditing(false) } }} />
        ) : (
          <span className="todo-title" onClick={() => setEditing(true)}>{todo.title}</span>
        )}
        {!compact && todo.notes && <span className="todo-notes">{todo.notes}</span>}
      </div>
      <div className="todo-meta">
        {showProject && todo.project_id && <ProjectChip projectId={todo.project_id} />}
        <label className={`todo-due ${due.cls}`} title="Due date">
          <Calendar size={11} />
          <span>{due.text || 'no date'}</span>
          <input type="date" value={todo.due ?? ''} onChange={(e) => void updateTodo(todo.id, e.target.value ? { due: e.target.value } : { clear_due: true })} />
        </label>
        {!compact && (
          <select className="todo-prio" value={todo.priority} onChange={(e) => void updateTodo(todo.id, { priority: Number(e.target.value) })} title="Priority">
            <option value={1}>P1</option><option value={2}>P2</option><option value={3}>P3</option>
          </select>
        )}
        <button className="icon-btn ghost danger" onClick={() => void deleteTodo(todo.id)}><Trash2 size={13} /></button>
      </div>
    </div>
  )
}
