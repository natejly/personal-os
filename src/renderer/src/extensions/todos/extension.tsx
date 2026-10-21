import { ListChecks } from '../../icons'
import type { UiApi } from '../host'
import { useStore } from '../../store'
import { useTodos } from './store'
import TodosView from './TodosView'
import { def as todosWidget } from '../../canvas/widgets/todos'

export function activate(ui: UiApi): void {
  ui.command({
    id: 'todos:refresh',
    label: 'Refresh lists',
    run: () => {
      const { tasksSync, google, runTasksSync } = useStore.getState()
      if (tasksSync?.config.enabled && google?.connected) void runTasksSync()
      useTodos.setState((s) => ({ tick: s.tick + 1 }))
    }
  })
  ui.view({
    id: 'todos',
    label: 'Lists',
    description: 'To-dos, synced with Google Tasks',
    icon: <ListChecks size={15} />,
    Component: TodosView,
    nav: { order: 12 },
    widget: todosWidget
  })
}
