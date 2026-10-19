import type { UiApi } from '../host'
import DigestSettings from './SettingsPanel'
import DigestTab from './DigestTab'

/** History digest: a bounded digest of what the user has told the assistant. Everything it registers is hidden while the extension is off. */
export function activate(ui: UiApi): void {
  // The settings host does not take a group or order yet; the panel is registered plainly (context group, order 70 wanted).
  ui.settingsPanel({ id: 'digest', label: 'History digest', keys: 'memory history digest summary tokens rebuild', Component: DigestSettings })
  ui.slot('sidepanel.tab', { id: 'history-digest', order: 35, label: 'History digest', Component: DigestTab })
}
