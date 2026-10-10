import { Inbox, MessageSquare } from 'lucide-react'
import { useStore } from '../store'
import { inboxBadge } from '../lib/inboxBadge'

/** The last thing in every title bar: the agent inbox (with its unread count), then the ⌘I page agent panel. */
export default function AppSwitcher(): JSX.Element {
  const open = useStore((s) => s.pageAgentOpen)
  const toggle = useStore((s) => s.togglePageAgent)
  const unread = useStore((s) => inboxBadge(s.agentInbox))
  const openInbox = (): void => {
    useStore.getState().setView('home')
    // The inbox renders with Today; scroll to it once the view has mounted.
    setTimeout(() => document.getElementById('agent-inbox')?.scrollIntoView({ block: 'start', behavior: 'smooth' }), 50)
  }
  return (
    <div className="app-switcher no-drag">
      <button className="icon-btn inbox-btn" title={unread ? `Agent inbox (${unread} new)` : 'Agent inbox'}
        aria-label={unread ? `Agent inbox, ${unread} new` : 'Agent inbox'} onClick={openInbox}>
        <Inbox size={15} />
        {unread > 0 && <span className="inbox-btn-count" aria-hidden="true">{unread > 99 ? '99+' : unread}</span>}
      </button>
      <button className={`icon-btn ${open ? 'on' : ''}`} title="Quick chat (⌘I)" aria-label="Quick chat (⌘I)" aria-pressed={open} onClick={toggle}>
        <MessageSquare size={15} />
      </button>
    </div>
  )
}
