/** The todos extension's store slice: the rows the Lists view, the calendar overlay and the canvas windows share. */
import { create } from 'zustand'
import type { Todo } from '@shared/types'
import { api, type Scope } from '../../lib/api'
import { useStore } from '../../store'
import { viewHidden } from '../../moduleToggles'

interface TodosState {
  todos: Todo[]
  /** Bumped (debounced) when the backend says todos changed elsewhere; the open Todos view re-reads on it. */
  tick: number
  refreshTodos: (scope?: Scope, includeDone?: boolean, sort?: 'due' | 'urgency') => Promise<void>
  addTodo: (t: Parameters<typeof api.todos.create>[0]) => Promise<void>
  updateTodo: (id: string, patch: Parameters<typeof api.todos.update>[1]) => Promise<void>
  deleteTodo: (id: string) => Promise<void>
}

let seq = 0

export const useTodos = create<TodosState>()((set, get) => ({
  todos: [],
  tick: 0,
  // Latest request wins: a slower, older fetch (another scope, or the mount's) must not overwrite the one the view asked for last.
  refreshTodos: async (scope = 'all', includeDone = false, sort = 'due') => {
    // The routes are not mounted while Lists is off; callers that refresh alongside other data must not fail on it.
    if (viewHidden(useStore.getState().settings, 'todos')) return
    const n = ++seq
    const rows = await api.todos.list(scope, includeDone, '', sort)
    if (n === seq) set({ todos: rows })
  },
  addTodo: async (t) => {
    await api.todos.create(t)
    await Promise.all([get().refreshTodos(), useStore.getState().refreshDashboard()])
  },
  updateTodo: async (id, patch) => {
    const recurs = patch.done === true && !!get().todos.find((x) => x.id === id)?.repeat
    // Completing is shown at once (a 500-row list otherwise waits a full round trip to react); put back if the write fails.
    const before = get().todos.find((x) => x.id === id)
    const optimistic = typeof patch.done === 'boolean' && before
    if (optimistic) set((s) => ({ todos: s.todos.map((x) => (x.id === id ? { ...x, done: patch.done ? 1 : 0 } : x)) }))
    let t
    try { t = await api.todos.update(id, patch) } catch (e) {
      if (optimistic) set((s) => ({ todos: s.todos.map((x) => (x.id === id ? before : x)) }))
      throw e
    }
    if (recurs) void get().refreshTodos()  // the next instance was just created server-side
    set((s) => ({ todos: s.todos.map((x) => (x.id === id ? t : x)) }))
    useStore.setState((s) => ({ dashboard: s.dashboard && { ...s.dashboard, todos: s.dashboard.todos.map((x) => (x.id === id ? t : x)).filter((x) => !x.done) } }))
    if ('done' in patch || 'due' in patch || 'clear_due' in patch) void useStore.getState().refreshDashboard()  // todo_stats: the nav badge and Today card
  },
  deleteTodo: async (id) => {
    const title = get().todos.find((x) => x.id === id)?.title
    await api.todos.delete(id)
    set((s) => ({ todos: s.todos.filter((x) => x.id !== id) }))
    useStore.setState((s) => ({ dashboard: s.dashboard && { ...s.dashboard, todos: s.dashboard.todos.filter((x) => x.id !== id) } }))
    void useStore.getState().refreshDashboard()
    useStore.getState().offerUndo(title ? `todo “${title}”` : 'todo', [{ type: 'todo', id }])
  }
}))
