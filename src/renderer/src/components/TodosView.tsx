import { useEffect, useState } from 'react'
import { Plus, CheckSquare, PanelLeftOpen, RefreshCw } from 'lucide-react'
import { useStore, type Scope } from '../store'
import TodoItem from './TodoItem'
import ScopeSelect from './ScopeSelect'
import SmartTextarea from './SmartTextarea'
import { localDay } from './CalendarWeek'
import SendToSpace from './SendToSpace'
import { lines, usePageContext } from '../lib/pageContext'

export default function TodosView(): JSX.Element {
  const todos = useStore((s) => s.todos)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const tasksSync = useStore((s) => s.tasksSync)
  const { refreshTodos, addTodo, toggleSidebar, refreshTasksSync, runTasksSync } = useStore()
  const [scope, setScope] = useState<Scope>('all')
  const [showDone, setShowDone] = useState(false)
  const [title, setTitle] = useState('')
  const [due, setDue] = useState('')
  const [priority, setPriority] = useState(2)

  useEffect(() => { void refreshTodos(scope, showDone) }, [scope, showDone, refreshTodos])
  useEffect(() => { void refreshTasksSync() }, [refreshTasksSync])

  const add = async (): Promise<void> => {
    if (!title.trim()) return
    await addTodo({ title, due: due || null, priority, project_id: scope === 'all' || scope === 'personal' ? null : scope })
    setTitle(''); setDue('')
    await refreshTodos(scope, showDone)
  }

  const open = todos.filter((t) => !t.done)
  const todayKey = localDay()
  const overdue = open.filter((t) => t.due && new Date(t.due + 'T00:00:00') < new Date(new Date().toDateString()))
  const today = open.filter((t) => t.due === todayKey)
  const upcoming = open.filter((t) => t.due && !overdue.includes(t) && !today.includes(t))
  const someday = open.filter((t) => !t.due)
  const done = todos.filter((t) => t.done)

  const fmt = (t: typeof todos[number]): string =>
    `${t.title} (\`${t.id}\`${t.due ? `, due ${t.due}` : ''}${t.priority !== 2 ? `, priority ${t.priority}` : ''})`
  usePageContext(() => ({
    view: 'todos',
    label: 'Todos',
    detail: [
      overdue.length ? `Overdue:\n${lines(overdue, fmt)}` : '',
      today.length ? `Due today (${todayKey}):\n${lines(today, fmt)}` : '',
      upcoming.length ? `Upcoming:\n${lines(upcoming, fmt)}` : '',
      someday.length ? `No date:\n${lines(someday, fmt)}` : '',
      open.length ? '' : 'Nothing open.'
    ].filter(Boolean).join('\n\n'),
    refs: open.slice(0, 40).map((t) => ({ kind: 'todo', id: t.id, name: t.title })),
    hints: ['What should I do first?', 'Reschedule the overdue ones to tomorrow', 'Break the biggest one into steps']
  }), [todos, todayKey])

  const Section = ({ label, items }: { label: string; items: typeof todos }): JSX.Element | null =>
    items.length ? (
      <section className="todo-section">
        <h4 className="section-h">{label} <span>{items.length}</span></h4>
        {items.map((t) => <TodoItem key={t.id} todo={t} />)}
      </section>
    ) : null

  return (
    <main className="page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" aria-label="Show sidebar" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><CheckSquare size={16} /> Todos</h2>
        <div className="no-drag header-right">
          {tasksSync?.config.enabled && (
            <button className="icon-btn" onClick={() => void runTasksSync()} disabled={tasksSync.syncing}
              title={tasksSync.last_error ? `Google Tasks sync failed: ${tasksSync.last_error}` : 'Sync with Google Tasks now'}>
              <RefreshCw size={14} className={tasksSync.syncing ? 'spin' : ''} />
            </button>
          )}
          <SendToSpace items={[{ kind: 'todos' }]} />
          <label className="check"><input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} /> show done</label>
          <ScopeSelect value={scope} onChange={setScope} />
        </div>
      </header>
      <div className="page-body">
        <div className="add-row">
          <SmartTextarea
            kind="todo"
            className="smart-ta-line"
            minChars={6}
            rows={1}
            value={title}
            onChange={setTitle}
            placeholder="Add a todo…"
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void add() } }}
          />
          <input type="date" aria-label="Due date (optional)" value={due} onChange={(e) => setDue(e.target.value)} className="date-input" />
          <select aria-label="Priority" value={priority} onChange={(e) => setPriority(Number(e.target.value))}><option value={1}>P1</option><option value={2}>P2</option><option value={3}>P3</option></select>
          <button className="primary-btn" onClick={() => void add()} disabled={!title.trim()}><Plus size={14} /> Add</button>
        </div>
        {todos.length === 0 && <p className="empty-hint big">No todos yet.</p>}
        <Section label="Overdue" items={overdue} />
        <Section label="Today" items={today} />
        <Section label="Upcoming" items={upcoming} />
        <Section label="Someday" items={someday} />
        {showDone && <Section label="Done" items={done} />}
      </div>
    </main>
  )
}
