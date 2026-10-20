import { Mail } from '../../icons'
import type { UiApi } from '../host'
import { ComposeCard, OutboxCard, ReadCard, SearchCard } from './EmailCard'
import { useStore } from '../../store'
import { requestViewCommand } from '../../lib/viewCommands'
import MailView from './MailView'

export function activate(ui: UiApi): void {
  ui.view({
    id: 'mail',
    label: 'Mail',
    description: 'Gmail inbox',
    icon: <Mail size={15} />,
    Component: MailView,
    nav: { order: 16 }
  })
  ui.command({ id: 'mail:refresh', label: 'Refresh mail', run: () => { useStore.getState().setView('mail'); requestViewCommand('mail:refresh') } })
  ui.command({ id: 'mail:rescan', label: 'Re-scan mail threads', run: () => { useStore.getState().setView('mail'); requestViewCommand('mail:rescan') } })
  ui.toolCard('gmail_send', ComposeCard)
  ui.toolCard('gmail_draft', ComposeCard)
  ui.toolCard('gmail_outbox', OutboxCard)
  ui.toolCard('gmail_search', SearchCard)
  ui.toolCard('gmail_read', ReadCard)
}
