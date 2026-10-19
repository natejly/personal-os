/** The Claude Code extension's frontend half: a sessions view with live timelines, tool cards, and a settings panel. */
import { Bot } from '../../icons'
import type { UiApi } from '../host'
import ClaudeCodeCard, { CARD_TOOLS } from './ClaudeCodeCard'
import ClaudeCodeView from './ClaudeCodeView'
import ClaudeCodeSettings from './SettingsPanel'
import { useClaudeCode, bindApi } from './store'
import type { CcEvent, CcSession } from './lib'

export function activate(ui: UiApi): void {
  bindApi(ui)
  ui.view({
    id: 'ext:claude-code',
    label: 'Claude Code',
    description: 'Sessions Claude Code is running for you',
    icon: <Bot size={15} />,
    Component: ClaudeCodeView,
    nav: { order: 40 }
  })
  for (const name of CARD_TOOLS) ui.toolCard(name, ClaudeCodeCard)
  ui.settingsPanel({ id: 'claude-code', label: 'Claude Code', keys: 'claude code sessions worktree budget turns permission', group: 'agent', order: 60, Component: ClaudeCodeSettings })
  // The backend publishes the full session on every change and one event at a time.
  ui.on('ext:claude-code:session', (data) => { if (data && typeof data === 'object') useClaudeCode.getState().upsert(data as CcSession) })
  ui.on('ext:claude-code:event', (data) => {
    const ev = data as CcEvent
    if (ev && typeof ev === 'object' && ev.session_id) useClaudeCode.getState().addEvents(ev.session_id, [ev])
  })
}
