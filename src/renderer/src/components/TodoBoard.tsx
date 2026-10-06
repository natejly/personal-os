import { useMemo, useState } from 'react'
import { Calendar, Plus, X } from 'lucide-react'
import type { Todo } from '@shared/types'
import { api } from '../lib/api'
import { useStore } from '../store'
import { dragProps, useDropTarget } from '../canvas/dnd'

/** The board's default columns; any other status a todo carries (a migrated board's own columns) gets one too. */
export const DEFAULT_STATUSES = ['Backlog', 'To do', 'In progress', 'Done']

export type BoardGroup = 'status' | 'list'

const dueLabel = (d: string): { label: string; overdue: boolean } => ({
  label: new Date(d + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
  overdue: new Date(d + 'T00:00:00') < new Date(new Date().toDateString())
})

interface Props {
  todos: Todo[]
  /** Columns by the todo's status, or one column per list. */
  groupBy: BoardGroup
  /** A card added here lands on this list (null = no list). */
  list: string | null
  /** The project a card added here belongs to. */
  projectId: string | null
  /** Called after any write, so the host can refetch. */
  onChanged: () => void | Promise<void>
}

interface ColProps {
  name: string
  label: string
  cards: Todo[]
  groupBy: BoardGroup
  onAdd: (title: string, col: string) => void
  onMove: (id: string, col: string, before: string | null) => void
}

function Column({ name, label, cards, groupBy, onAdd, onMove }: ColProps): JSX.Element {
  const [title, setTitle] = useState('')
  const drop = useDropTarget(['todo'], (p, e) => {
    if (!p) return
    const els = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('.kcard-wrap'))
    const before = els.find((el) => e.clientY < el.getBoundingClientRect().top + el.offsetHeight / 2 && el.dataset.id !== p.id)
    onMove(p.id, name, before?.dataset.id ?? null)
  })
  const toggle = (t: Todo): void => { void useStore.getState().updateTodo(t.id, { done: !t.done }) }
  return (
    <div className={`kcol ${drop.over ? 'over drop-over' : ''}`} {...drop.handlers}>
      <header>
        <span className="kcol-name">{label}</span>
        <span className="count">{cards.length}</span>
      </header>
      <div className="kcol-cards">
        {cards.map((c) => (
          <div key={c.id} data-id={c.id} className="kcard-wrap">
            <div className={`kcard p${c.priority}${c.done ? ' done' : ''}`} {...dragProps({ kind: 'todo', id: c.id, label: c.title, projectId: c.project_id })}>
              <div className="kcard-title">
                <input type="checkbox" aria-label={`Done: ${c.title}`} checked={!!c.done} onChange={() => toggle(c)} /> {c.title}
              </div>
              <div className="kcard-meta">
                {c.due && <span className={`tag ${!c.done && dueLabel(c.due).overdue ? 'overdue' : ''}`}><Calendar size={10} />{dueLabel(c.due).label}</span>}
                {groupBy === 'list' && <span className="tag">{c.status}</span>}
                {(c.tags ?? []).map((l) => <span key={l} className="tag">{l}</span>)}
                {c.priority === 1 && <span className="tag p1">P1</span>}
                <span style={{ flex: 1 }} />
                <button className="icon-btn ghost sm" title="Delete" aria-label={`Delete ${c.title}`} onClick={() => void useStore.getState().deleteTodo(c.id)}><X size={11} /></button>
              </div>
            </div>
          </div>
        ))}
        <div className="kcol-addbtn">
          <Plus size={13} />
          <input className="widget-input" placeholder="Add todo" aria-label={`Add todo to ${label}`} value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && title.trim()) { onAdd(title.trim(), name); setTitle('') } if (e.key === 'Escape') setTitle('') }} />
        </div>
      </div>
    </div>
  )
}

/** Kanban view of todos. Shared by the Todos page and the todos canvas window; a drag is the todo's own drag payload. */
export default function TodoBoard({ todos, groupBy, list, projectId, onChanged }: Props): JSX.Element {
  const toast = useStore((s) => s.toast)
  const fail = (e: unknown): void => toast((e as Error).message, 'error')

  const cols = useMemo(() => {
    const names = groupBy === 'status'
      ? [...DEFAULT_STATUSES.slice(0, -1), ...todos.map((t) => t.status).filter((s) => !DEFAULT_STATUSES.includes(s)), 'Done']
      : ['', ...todos.map((t) => t.list_name ?? '')]
    return [...new Set(names)]
  }, [todos, groupBy])

  const keyOf = (t: Todo): string => (groupBy === 'status' ? t.status : t.list_name ?? '')
  const byCol = useMemo(() => {
    const m: Record<string, Todo[]> = {}
    for (const t of todos) (m[groupBy === 'status' ? t.status : t.list_name ?? ''] ??= []).push(t)
    for (const k of Object.keys(m)) m[k].sort((a, b) => a.position - b.position || b.created_at - a.created_at)
    return m
  }, [todos, groupBy])

  const move = (id: string, col: string, before: string | null): void => {
    const t = todos.find((x) => x.id === id)
    if (!t) return
    const run = groupBy === 'status'
      ? api.todos.move(id, col, before)
      : keyOf(t) === col ? Promise.resolve() : api.todos.update(id, col ? { list_name: col } : { clear_list: true })
    void Promise.resolve(run).then(onChanged).catch(fail)
  }
  const add = (title: string, col: string): void => {
    void api.todos.create({ title, project_id: projectId, ...(groupBy === 'status' ? { status: col, list_name: list } : { list_name: col || null }) })
      .then(onChanged).catch(fail)
  }

  return (
    <div className="kanban">
      {cols.map((c) => (
        <Column key={c || '__none'} name={c} label={c || 'No list'} cards={byCol[c] ?? []} groupBy={groupBy} onAdd={add} onMove={move} />
      ))}
    </div>
  )
}
