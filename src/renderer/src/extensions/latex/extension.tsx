import { Sigma } from '../../icons'
import type { UiApi } from '../host'
import LatexView from './LatexView'
import ProjectFiles from './ProjectFiles'

export function activate(ui: UiApi): void {
  ui.view({
    id: 'ext:latex',
    label: 'LaTeX',
    description: 'Write and compile LaTeX documents',
    icon: <Sigma size={15} />,
    Component: LatexView,
    nav: { order: 19 }
  })
  ui.slot('project.files', { id: 'latex', order: 0, Component: ProjectFiles })
}
