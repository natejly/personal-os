import { ListChecks } from '../../icons'
import type { UiApi } from '../host'
import TodosView from './TodosView'
import { def as todosWidget } from '../../canvas/widgets/todos'

export function activate(ui: UiApi): void {
  ui.view({
    id: 'todos',
    label: 'Lists',
    description: 'To-dos, synced with Google Tasks',
    icon: <ListChecks size={15} />,
    Component: TodosView,
    nav: { order: 12, badge: (s) => s.dashboard?.todo_stats?.open || null },
    widget: todosWidget
  })
}
