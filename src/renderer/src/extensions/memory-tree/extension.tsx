import type { UiApi } from '../host'
import DigestSettings from './SettingsPanel'
import DigestTab from './DigestTab'

/** History digest: a bounded digest of what the user has told the assistant. Everything it registers is hidden while the extension is off. */
export function activate(ui: UiApi): void {
  ui.settingsPanel({ id: 'digest', label: 'History digest', keys: 'memory history digest summary tokens rebuild', group: 'context', order: 70, Component: DigestSettings })
  ui.slot('sidepanel.tab', { id: 'history-digest', order: 35, label: 'History digest', Component: DigestTab })
}
