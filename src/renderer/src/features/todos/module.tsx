import { ListChecks } from 'lucide-react'
import type { ModuleDef } from '../../shell/types'
import TodosView from '../../components/TodosView'
import { def as todosWidget } from '../../canvas/widgets/todos'
import TodosCard from './TodosCard'

export const todosModule: ModuleDef = {
  key: 'todos',
  label: 'Lists',
  icon: <ListChecks size={15} />,
  view: { id: 'todos', Component: TodosView, optional: true },
  nav: { section: 'apps', order: 0, badge: (s) => s.dashboard?.todo_stats?.open ?? null },
  widget: todosWidget,
  home: { key: 'todos', label: 'Lists', Card: TodosCard },
}
