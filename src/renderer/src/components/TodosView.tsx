import { useEffect, useState } from 'react'
import { Plus, ListChecks, RefreshCw, Bookmark, X, ChevronDown, KanbanSquare, List, SlidersHorizontal, Pencil, Trash2, Inbox, Layers } from 'lucide-react'
import type { TodoFilter, TodoRepeat } from '@shared/types'
import { api } from '../lib/api'
import { useStore, type Scope } from '../store'
import TodoItem from './TodoItem'
import ScopeSelect from './ScopeSelect'
import SmartTextarea from './SmartTextarea'
import { localDay } from './CalendarWeek'
import SendToSpace from './SendToSpace'
import { lines, usePageContext } from '../lib/pageContext'
import AppSwitcher from './AppSwitcher'
import PlannerPanel from './PlannerPanel'
import TodoBoard, { type BoardGroup } from './TodoBoard'
import { clearHandoff, peekHandoff } from '../lib/handoff'
import SidebarToggle from './SidebarToggle'
import { rowButton } from '../lib/rowButton'

/** The unlisted todos are the default list; this is what it is called in the rail. */
const DEFAULT_LIST = 'Todos'

export default function TodosView(): JSX.Element {
  const todos = useStore((s) => s.todos)
  const tasksSync = useStore((s) => s.tasksSync)
  const googleConnected = useStore((s) => !!s.google?.connected)
  const { refreshTodos, addTodo, refreshTasksSync, runTasksSync, toast } = useStore()
  const [scope, setScope] = useState<Scope>('all')
  const [showDone, setShowDone] = useState(false)
  // A canvas todos window in board view hands its view over on Expand.
  const [view, setView] = useState<'list' | 'board'>(() => (peekHandoff('todos-view') === 'board' ? 'board' : 'list'))
  const [groupBy, setGroupBy] = useState<BoardGroup>('status')
  // null = every list; '' = the default (unlisted) todos; otherwise a named list.
  const [list, setList] = useState<string | null>(null)
  const [lists, setLists] = useState<string[]>([])
  const [newList, setNewList] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<{ from: string; to: string } | null>(null)
  useEffect(() => { clearHandoff('todos-view') }, [])
  const board = view === 'board'
  const [title, setTitle] = useState('')
  const [due, setDue] = useState('')
  const [priority, setPriority] = useState(2)
  const [repeat, setRepeat] = useState<'' | TodoRepeat['unit']>('')
  const [more, setMore] = useState(false)
  const [sort, setSort] = useState<'due' | 'urgency'>('due')
  const [tag, setTag] = useState('')
  const [saved, setSaved] = useState<TodoFilter[]>([])
  useEffect(() => { void api.todos.filters().then(setSaved).catch(() => undefined) }, [])
  const saveFilter = async (): Promise<void> => {
    const name = tag.trim()
    if (!name) return
    try { const f = await api.todos.saveFilter({ name: `#${name}`, tag: name }); setSaved((s) => [...s, f]) } catch (e) { toast((e as Error).message, 'error') }
  }
  const dropFilter = async (id: string): Promise<void> => { await api.todos.deleteFilter(id).catch(() => undefined); setSaved((s) => s.filter((f) => f.id !== id)) }
  // The store starts with no todos, which is not the same as there being none.
  const [loaded, setLoaded] = useState(false)

  const todosTick = useStore((s) => s.todosTick)
  useEffect(() => { void refreshTodos(scope, showDone || board, sort).finally(() => setLoaded(true)) }, [scope, showDone, board, sort, refreshTodos, todosTick])
  useEffect(() => { void refreshTasksSync() }, [refreshTasksSync])
  // Lists live on their own (an empty one still exists), so they are read apart from the todos.
  useEffect(() => { void api.todos.lists().then(setLists).catch(() => undefined) }, [todosTick])

  const fail = (e: unknown): void => toast((e as Error).message, 'error')
  const createList = async (): Promise<void> => {
    const name = (newList ?? '').trim()
    setNewList(null)
    if (!name) return
    try { setLists(await api.todos.createList(name)); setList(name) } catch (e) { fail(e) }
  }
  const renameList = async (): Promise<void> => {
    const r = renaming
    setRenaming(null)
    if (!r || !r.to.trim() || r.to.trim() === r.from) return
    try {
      setLists(await api.todos.renameList(r.from, r.to.trim()))
      if (list === r.from) setList(r.to.trim())
      await refreshTodos(scope, showDone || board, sort)
    } catch (e) { fail(e) }
  }
  const deleteList = async (name: string): Promise<void> => {
    try {
      setLists(await api.todos.deleteList(name))
      if (list === name) setList('')
      await refreshTodos(scope, showDone || board, sort)
      toast(`“${name}” removed; its items are back on ${DEFAULT_LIST}`)
    } catch (e) { fail(e) }
  }

  // Cleared before the request, so a second Enter (or Enter then Add) cannot post the same todo twice;
  // handed back if the request fails.
  const add = async (): Promise<void> => {
    const t = title, d = due
    if (!t.trim()) return
    setTitle(''); setDue('')
    try {
      await addTodo({ title: t, due: d || null, priority, project_id: scope === 'all' || scope === 'personal' ? null : scope, repeat: repeat ? { every: 1, unit: repeat, mode: 'from_due' } : null, list_name: list || null })
    } catch (e) {
      setTitle(t); setDue(d)
      return fail(e)
    }
    await refreshTodos(scope, showDone || board, sort)
  }

  const shown = todos.filter((t) => (!tag || t.tags?.includes(tag)) && (list === null || (t.list_name ?? '') === list))
  const open = shown.filter((t) => !t.done)
  const todayKey = localDay()
  const overdue = open.filter((t) => t.due && new Date(t.due + 'T00:00:00') < new Date(new Date().toDateString()))
  const today = open.filter((t) => t.due === todayKey)
  const upcoming = open.filter((t) => t.due && !overdue.includes(t) && !today.includes(t))
  const someday = open.filter((t) => !t.due)
  const done = shown.filter((t) => t.done)
  const listLabel = list === null ? 'all lists' : list || DEFAULT_LIST
  const openCount = (name: string): number => todos.filter((t) => !t.done && (t.list_name ?? '') === name).length

  const fmt = (t: typeof todos[number]): string =>
    `${t.title} (\`${t.id}\`${t.due ? `, due ${t.due}` : ''}${t.priority !== 2 ? `, priority ${t.priority}` : ''}${t.list_name ? `, list ${t.list_name}` : ''})`
  usePageContext(() => ({
    view: 'todos',
    label: 'Lists',
    detail: [
      list === null ? `Lists: ${[DEFAULT_LIST, ...lists].join(', ')}` : `Showing list: ${listLabel}`,
      overdue.length ? `Overdue:\n${lines(overdue, fmt)}` : '',
      today.length ? `Due today (${todayKey}):\n${lines(today, fmt)}` : '',
      upcoming.length ? `Upcoming:\n${lines(upcoming, fmt)}` : '',
      someday.length ? `No date:\n${lines(someday, fmt)}` : '',
      open.length ? '' : 'Nothing open.'
    ].filter(Boolean).join('\n\n'),
    refs: open.slice(0, 40).map((t) => ({ kind: 'todo', id: t.id, name: t.title })),
    hints: ['What should I do first?', 'Reschedule the overdue ones to tomorrow', 'Break the biggest one into steps']
  }), [todos, todayKey, list, lists])

  // A subtask indents under its parent when the parent is in the same section.
  const depthOf = (t: typeof todos[number], items: typeof todos): number => {
    let d = 0, cur = t
    while (cur.parent_id && d < 5) {
      const p = items.find((x) => x.id === cur.parent_id)
      if (!p) break
      d++; cur = p
    }
    return d
  }
  // A plain function, not a component: a component declared here would be a new type every render and
  // remount every TodoItem, losing an open title edit or date picker whenever the list refreshes.
  const section = (label: string, items: typeof todos): JSX.Element | null =>
    items.length ? (
      <section className="todo-section">
        <h4 className="section-h">{label} <span>{items.length}</span></h4>
        {items.map((t) => <TodoItem key={t.id} todo={t} depth={depthOf(t, items)} onTag={setTag} />)}
      </section>
    ) : null

  // Rail rows hold their own buttons, so they are divs that act as buttons rather than nested buttons.
  const railRow = (name: string | null, label: string, icon: JSX.Element, count: number | null): JSX.Element => (
    <div key={name ?? '__all'} className={`list-row ${list === name ? 'active' : ''}`} aria-current={list === name ? 'true' : undefined} {...rowButton(() => setList(name))}>
      {icon}
      {renaming && name && renaming.from === name ? (
        <input autoFocus aria-label={`Rename list ${name}`} value={renaming.to} onChange={(e) => setRenaming({ from: name, to: e.target.value })}
          onBlur={() => void renameList()} onKeyDown={(e) => { if (e.key === 'Enter') void renameList(); if (e.key === 'Escape') setRenaming(null) }} onClick={(e) => e.stopPropagation()} />
      ) : <span>{label}</span>}
      {count !== null && count > 0 && !(renaming && renaming.from === name) && <span className="count">{count}</span>}
      {name && !(renaming && renaming.from === name) && <>
        <button className="icon-btn ghost xs" title="Rename list" aria-label={`Rename list ${name}`} onClick={(e) => { e.stopPropagation(); setRenaming({ from: name, to: name }) }}><Pencil size={11} /></button>
        <button className="icon-btn ghost xs danger" title={`Remove list (its items go back to ${DEFAULT_LIST})`} aria-label={`Remove list ${name}`} onClick={(e) => { e.stopPropagation(); void deleteList(name) }}><Trash2 size={11} /></button>
      </>}
    </div>
  )
  const showMore = more || !!due || priority !== 2 || !!repeat

  return (
    <main className="page">
      <header className="page-header drag">
        <SidebarToggle />
        <h2><ListChecks size={16} /> Lists</h2>
        <div className="no-drag header-right">
          {tasksSync?.config.enabled && googleConnected && (
            <button className="icon-btn" onClick={() => void runTasksSync()} disabled={tasksSync.syncing}
              title={tasksSync.last_error ? `Google Tasks sync failed: ${tasksSync.last_error}` : 'Sync with Google Tasks now'}>
              <RefreshCw size={14} className={tasksSync.syncing ? 'spin' : ''} />
            </button>
          )}
          <SendToSpace items={[{ kind: 'todos' }]} />
          {!board && (
            <label className={`chip-check ${showDone ? 'on' : ''}`}>
              <input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} /> Show done
            </label>
          )}
          <label className="model-picker" title="Urgency scores due date, priority and age">
            <select aria-label="Sort" value={sort} onChange={(e) => setSort(e.target.value as 'due' | 'urgency')}><option value="due">Sort: Due</option><option value="urgency">Sort: Urgency</option></select>
            <ChevronDown size={14} />
          </label>
          <div className="seg" role="group" aria-label="View">
            <button className={`icon-btn${board ? '' : ' on'}`} aria-pressed={!board} title="List view" aria-label="List view" onClick={() => setView('list')}><List size={14} /></button>
            <button className={`icon-btn${board ? ' on' : ''}`} aria-pressed={board} title="Board view" aria-label="Board view" onClick={() => setView('board')}><KanbanSquare size={14} /></button>
          </div>
          {board && <label className="model-picker"><select aria-label="Columns" value={groupBy} onChange={(e) => setGroupBy(e.target.value as BoardGroup)}><option value="status">Columns: Status</option><option value="list">Columns: List</option></select><ChevronDown size={14} /></label>}
          <ScopeSelect value={scope} onChange={setScope} />
        </div>
        <AppSwitcher />
      </header>
      <div className="page-body wide lists-body">
        <aside className="list-rail" aria-label="Lists">
          {railRow(null, 'All', <Layers size={14} />, null)}
          {railRow('', DEFAULT_LIST, <Inbox size={14} />, openCount(''))}
          {lists.map((n) => railRow(n, n, <List size={14} />, openCount(n)))}
          {newList === null ? (
            <button className="list-row list-new" onClick={() => setNewList('')}><Plus size={14} /><span>New list</span></button>
          ) : (
            <div className="list-row">
              <Plus size={14} />
              <input autoFocus aria-label="New list name" placeholder="List name" value={newList} onChange={(e) => setNewList(e.target.value)}
                onBlur={() => void createList()} onKeyDown={(e) => { if (e.key === 'Enter') void createList(); if (e.key === 'Escape') setNewList(null) }} />
            </div>
          )}
        </aside>
        <div className="lists-main">
          <PlannerPanel />
          {/* The add row reads as the next item of the list: a plus where the check circle sits, Enter adds. */}
          <div className="todo-add">
            <Plus size={16} />
            <SmartTextarea
              kind="todo"
              className="smart-ta-line"
              variant="bare"
              minChars={6}
              rows={1}
              value={title}
              onChange={setTitle}
              placeholder={list === null ? `Add to ${DEFAULT_LIST}…` : `Add to ${listLabel}…`}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void add() } }}
            />
            <button className={`icon-btn sm${showMore ? ' on' : ''}`} aria-pressed={showMore} title="Due date, priority, repeat" aria-label="Due date, priority, repeat" onClick={() => setMore(!showMore)}><SlidersHorizontal size={14} /></button>
            {title.trim() && <button className="primary-btn" onClick={() => void add()}>Add</button>}
          </div>
          {showMore && (
            <div className="todo-add-more">
              <input type="date" aria-label="Due date (optional)" value={due} onChange={(e) => setDue(e.target.value)} />
              <select aria-label="Priority" value={priority} onChange={(e) => setPriority(Number(e.target.value))}><option value={1}>P1</option><option value={2}>P2</option><option value={3}>P3</option></select>
              <select aria-label="Repeat" value={repeat} onChange={(e) => setRepeat(e.target.value as '' | TodoRepeat['unit'])}><option value="">No repeat</option><option value="day">Daily</option><option value="week">Weekly</option><option value="month">Monthly</option><option value="year">Yearly</option></select>
            </div>
          )}
          {(tag || saved.length > 0) && (
            <div className="todo-filters">
              {saved.map((f) => (
                <span key={f.id} className={`todo-tag ${tag === f.tag ? 'on' : ''}`}>
                  <button onClick={() => setTag(tag === f.tag ? '' : f.tag ?? '')}>{f.name}</button>
                  <button aria-label={`Remove filter ${f.name}`} onClick={() => void dropFilter(f.id)}><X size={10} /></button>
                </span>
              ))}
              {tag && <>
                <span className="todo-tag on">#{tag} <button aria-label="Clear tag filter" onClick={() => setTag('')}><X size={10} /></button></span>
                {!saved.some((f) => f.tag === tag) && <button className="icon-btn ghost" title="Save this filter" aria-label="Save this filter" onClick={() => void saveFilter()}><Bookmark size={13} /></button>}
              </>}
            </div>
          )}
          {/* No button: the add row right above is the action, and a second filled one would compete with it. */}
          {loaded && shown.length === 0 && (
            <div className="empty-state">
              <ListChecks size={28} />
              <h2>{showDone || board ? `Nothing on ${listLabel}` : 'Nothing open'}</h2>
              <p>Type something in the box above and press Enter, or ask the assistant to keep track of it for you.</p>
            </div>
          )}
          {board ? <TodoBoard todos={shown} groupBy={groupBy} list={list || null} projectId={scope === 'all' || scope === 'personal' ? null : scope} onChanged={() => refreshTodos(scope, true, sort)} />
            : sort === 'urgency' ? section('By urgency', open) : <>
            {section('Overdue', overdue)}
            {section('Today', today)}
            {section('Upcoming', upcoming)}
            {section('Someday', someday)}
          </>}
          {!board && showDone && section('Done', done)}
        </div>
      </div>
    </main>
  )
}
