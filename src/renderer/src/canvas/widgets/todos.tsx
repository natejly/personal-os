import { useEffect, useMemo, useRef, useState } from 'react'
import { CheckSquare, Plus } from 'lucide-react'
import type { DragKind, DragPayload, Todo } from '@shared/types'
import TodoItem from '../../components/TodoItem'
import SmartTextarea from '../../components/SmartTextarea'
import TodoBoard, { type BoardGroup } from '../../components/TodoBoard'
import { localDay } from '../../components/CalendarWeek'
import { useStore } from '../../store'
import type { Scope } from '../../lib/api'
import { useDropTarget } from '../dnd'
import type { WidgetDef, WidgetProps } from '../registry'

const ACCEPTS: DragKind[] = ['todo']

const midnight = (): number => {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}
const dueAt = (t: Todo): number => (t.due ? new Date(`${t.due}T00:00:00`).getTime() : Infinity)

interface Cfg { scope: Scope; includeDone: boolean; q: string; view: 'list' | 'board'; list: string; groupBy: BoardGroup }

/** `config` is whatever the backend last stored, so every field is read defensively. */
const cfg = (c: Record<string, unknown>): Cfg => ({
  scope: typeof c.scope === 'string' && c.scope ? c.scope : 'all',
  includeDone: c.includeDone === true,
  q: typeof c.q === 'string' ? c.q : '',
  view: c.view === 'board' ? 'board' : 'list',
  list: typeof c.list === 'string' ? c.list : '',
  groupBy: c.groupBy === 'list' ? 'list' : 'status'
})

function TodosWidget({ window: win, live, onConfig }: WidgetProps): JSX.Element {
  const c = cfg(win.config)
  const [q, setQ] = useState(c.q)
  const [draft, setDraft] = useState('')
  const loaded = useRef(false)
  const todos = useStore((s) => s.todos)
  const projects = useStore((s) => s.projects)
  const projectId = c.scope === 'all' || c.scope === 'personal' ? null : c.scope

  // One fetch, at the widest scope, and only once this window is actually on screen: every todos
  // window filters the same array client-side so two different scopes cannot thrash the same GET.
  useEffect(() => {
    if (!live || loaded.current) return
    loaded.current = true
    void useStore.getState().refreshTodos('all', true)
  }, [live])

  // The query is typed locally and persisted once the typing stops; one PUT per keystroke is not it.
  useEffect(() => {
    if (q === c.q) return
    const t = setTimeout(() => onConfig({ q }), 400)
    return () => clearTimeout(t)
  }, [q, c.q, onConfig])

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return todos
      .filter((t) => (c.includeDone || c.view === 'board' || !t.done)
        && (!c.list || t.list_name === c.list)
        && (c.scope === 'all' || (c.scope === 'personal' ? !t.project_id : t.project_id === c.scope))
        && (!needle || t.title.toLowerCase().includes(needle) || (t.notes ?? '').toLowerCase().includes(needle)))
      .sort((a, b) => dueAt(a) - dueAt(b) || a.priority - b.priority)
  }, [todos, c.scope, c.includeDone, c.view, c.list, q])
  const lists = useMemo(() => [...new Set(todos.map((t) => t.list_name).filter((n): n is string => !!n))].sort(), [todos])
  const board = c.view === 'board'

  const open = rows.filter((t) => !t.done)
  const cut = midnight()
  const day = localDay()
  const overdue = open.filter((t) => t.due && dueAt(t) < cut)
  const dueToday = open.filter((t) => t.due === day)
  const upcoming = open.filter((t) => t.due && dueAt(t) > cut && t.due !== day)
  const someday = open.filter((t) => !t.due)
  const done = rows.filter((t) => t.done)

  const add = async (): Promise<void> => {
    const title = draft.trim()
    if (!title) return
    setDraft('')
    const app = useStore.getState()
    await app.addTodo({ title, project_id: projectId, list_name: c.list || null })
    // `addTodo` refreshes at the default scope, which drops the done rows this window may be showing.
    await app.refreshTodos('all', true)
  }

  const onDrop = async (p: DragPayload | null): Promise<void> => {
    if (!p) return
    const app = useStore.getState()
    // A todo dragged in from another window joins this one's scope; under 'all' there is nothing to change.
    if (p.kind === 'todo' && c.scope !== 'all' && p.projectId !== projectId) {
      await app.updateTodo(p.id, projectId ? { project_id: projectId } : { clear_project: true })
    }
  }

  const drop = useDropTarget(ACCEPTS, (p) => void onDrop(p))

  // Off-screen or zoomed out: every row unmounts, taking its store subscription with it.
  if (!live) {
    return (
      <div className="proxy-card">
        <CheckSquare size={18} />
        <strong>{win.title || 'Todos'}</strong>
        <span>{open.length} open · paused while off-screen</span>
      </div>
    )
  }

  // A function, not a component: a component declared in a render body is a new type every render,
  // which would remount every row — and with it the inline title editor — on each keystroke.
  const section = (label: string, items: Todo[]): JSX.Element | null =>
    items.length ? (
      <section key={label}>
        <h4 className="section-h">{label} <span>{items.length}</span></h4>
        {items.map((t) => <TodoItem key={t.id} todo={t} compact showProject={c.scope === 'all'} />)}
      </section>
    ) : null

  return (
    <div className={drop.over ? 'widget drop-over' : 'widget'} {...drop.handlers}>
      <div className="widget-bar">
        <input className="widget-input" placeholder="Filter…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="widget-input" style={{ width: 'auto', maxWidth: 130 }} value={c.scope} title="Project"
          onChange={(e) => onConfig({ scope: e.target.value })}>
          <option value="all">All</option>
          <option value="personal">Personal</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        {lists.length > 0 && (
          <select className="widget-input" style={{ width: 'auto', maxWidth: 110 }} value={c.list} title="List" onChange={(e) => onConfig({ list: e.target.value })}>
            <option value="">All lists</option>
            {lists.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        )}
        {board && <button className={c.groupBy === 'list' ? 'widget-chip on' : 'widget-chip'} title="Columns by list instead of status" onClick={() => onConfig({ groupBy: c.groupBy === 'list' ? 'status' : 'list' })}>lists</button>}
        {!board && <button className={c.includeDone ? 'widget-chip on' : 'widget-chip'} title="Show completed"
          onClick={() => onConfig({ includeDone: !c.includeDone })}>done</button>}
        <button className={board ? 'widget-chip on' : 'widget-chip'} title="Board view" onClick={() => onConfig({ view: board ? 'list' : 'board' })}>board</button>
      </div>

      {board ? (
        <div className="widget-scroll">
          <TodoBoard todos={rows} groupBy={c.groupBy} list={c.list || null} projectId={projectId} onChanged={() => useStore.getState().refreshTodos('all', true)} />
        </div>
      ) : <div className="widget-scroll">
        {!rows.length && <p className="widget-sub">{q.trim() || c.scope !== 'all' ? 'Nothing matches.' : 'No todos yet.'}</p>}
        {section('Overdue', overdue)}
        {section('Today', dueToday)}
        {section('Upcoming', upcoming)}
        {section('Someday', someday)}
        {section('Done', done)}
      </div>}

      {!board && <div className="widget-bar">
        <SmartTextarea
          kind="todo"
          className="smart-ta-line"
          variant="bare"
          rows={1}
          minChars={6}
          value={draft}
          onChange={setDraft}
          placeholder="Add a todo…"
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void add() } }}
        />
        <button className="icon-btn sm" title="Add" disabled={!draft.trim()} onClick={() => void add()}><Plus size={14} /></button>
      </div>}
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'todos',
  label: 'Todos',
  icon: <CheckSquare size={18} />,
  defaultSize: { w: 380, h: 520 },
  minSize: { w: 280, h: 240 },
  chrome: 'full',
  defaultConfig: { scope: 'all', includeDone: false, q: '', view: 'list', list: '', groupBy: 'status' },
  accepts: ACCEPTS,
  Component: TodosWidget
}

export default TodosWidget
