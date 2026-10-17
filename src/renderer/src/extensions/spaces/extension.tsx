import { rowHidden } from '../../moduleToggles'
import type { UiApi } from '../host'
import SidebarSpaces from './SidebarSpaces'

export function activate(ui: UiApi): void {
  ui.slot('sidebar.section', { id: 'spaces', order: 10, Component: SidebarSpaces, when: (s) => !rowHidden(s.settings, 'spaces') })
}
