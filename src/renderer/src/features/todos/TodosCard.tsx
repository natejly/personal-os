import { ListChecks } from 'lucide-react'
import type { TodayDashboard } from '@shared/types'
import { useStore } from '../../store'
import TodoItem from '../../components/TodoItem'

/** The Today card for todos. HomeView gates it on `homeWidgets.todos`. */
export default function TodosCard({ data: d }: { data: TodayDashboard | null }): JSX.Element {
  const setView = useStore((s) => s.setView)
  return (
    <section className="widget">
      <header><ListChecks size={14} /> Lists <span className="muted small">{d?.todo_stats.open ?? 0} open{d?.todo_stats.overdue ? ` · ${d.todo_stats.overdue} overdue` : ''}</span><button className="link small" onClick={() => setView('todos')}>View all</button></header>
      {!d ? <p className="muted">Loading…</p> : d.todos.length === 0 ? <p className="muted">All clear.</p> : d.todos.slice(0, 8).map((t) => <TodoItem key={t.id} todo={t} compact />)}
    </section>
  )
}
