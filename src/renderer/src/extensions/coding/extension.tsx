import type { CodingSession, CodingSessionEvent } from '@shared/types'
import { notify } from '../../lib/notify'
import { useStore } from '../../store'
import type { UiApi } from '../host'
import CodingSessionCard, { CARD_TOOLS } from './CodingSessionCard'
import RecipesPanel from './RecipesPanel'
import { TerminalOverlay, TerminalTab } from './TerminalTab'
import { codingNoticeText, codingTransition, type CodingTransition } from './lib/codingSessions'
import { useCoding } from './store'
import { termAvailable } from './termRegistry'

/** Whether this build can host terminals; false until the app has said. */
let terminalsOn = false

/**
 * A coding session finished, failed or is waiting on the user: a toast in the app, plus an OS notice when the
 * window is not in front. Callers pass only real transitions (codingTransition), never a first sighting.
 */
function announce(kind: CodingTransition, c: CodingSession): void {
  const text = codingNoticeText(kind, c)
  const s = useStore.getState()
  s.toast(text, kind === 'failed' ? 'error' : 'info')
  if (s.settings.chatNotify === false || (typeof document !== 'undefined' && document.hasFocus())) return
  notify('Coding agent', text, {
    tag: `coding:${c.id}:${kind}`,
    onClick: () => { try { window.os.showMain() } catch { /* no bridge: window.focus() in notify() is the fallback */ } }
  })
}

export function activate(ui: UiApi): void {
  for (const name of CARD_TOOLS) ui.toolCard(name, CodingSessionCard)
  // Last in the side panel. It lists once the app has said it can host terminals.
  ui.slot('sidepanel.tab', { id: 'terminal', order: 60, label: 'Terminal', Component: TerminalTab, Overlay: TerminalOverlay, shown: (p) => !!p.conversationId, when: () => terminalsOn })
  void termAvailable().then((ok) => { terminalsOn = ok; if (ok) useStore.setState({}) })  // the nudge makes every slot re-read its `when`
  ui.on('coding_session', (data) => {
    const c = data as CodingSession
    const prev = useCoding.getState().sessions[c.id]
    useCoding.getState().upsert(c)
    const kind = codingTransition(prev, c)
    if (kind) announce(kind, c)
  })
  ui.on('coding_session_event', (data) => {
    const ev = data as CodingSessionEvent
    useCoding.getState().addEvents(ev.session_id, [ev])
  })
  ui.settingsPanel({ id: 'recipes', label: 'Coding recipes', keys: 'recipe coding', Component: RecipesPanel })
  ui.command({
    id: 'run-recipe', label: 'Run coding recipe...', hint: 'Coding',
    run: () => useStore.getState().openSettings('ext:coding:recipes')
  })
}
