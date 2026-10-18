import { Inbox, MessageSquare } from '../icons'
import { useStore } from '../store'
import { inboxBadge } from '../lib/inboxBadge'
import { tip } from '../lib/tip'
import TitleBarSlot from './TitleBarSlot'

/** The last thing in every title bar: the extensions' `titlebar.right` items, the agent inbox (with its unread count), then the ⌘I page agent panel. */
/** `last` replaces the quick-chat button, for a window that gives that slot to its own panel. */
export default function AppSwitcher({ last }: { last?: React.ReactNode } = {}): JSX.Element {
  const open = useStore((s) => s.pageAgentOpen)
  const toggle = useStore((s) => s.togglePageAgent)
  const unread = useStore((s) => inboxBadge(s.agentInbox))
  const openInbox = useStore((s) => s.openInbox)
  return (
    <div className="app-switcher no-drag">
      <TitleBarSlot side="right" />
      <button className="icon-btn inbox-btn" title={unread ? `Agent inbox (${unread} new)` : 'Agent inbox'}
        aria-label={unread ? `Agent inbox, ${unread} new` : 'Agent inbox'} onClick={openInbox}>
        <Inbox size={15} />
        {unread > 0 && <span className="inbox-btn-count" aria-hidden="true">{unread > 99 ? '99+' : unread}</span>}
      </button>
      {last ?? (
        <button className={`icon-btn ${open ? 'on' : ''}`} title={tip('Quick chat', 'page-agent')} aria-label="Quick chat" aria-pressed={open} onClick={toggle}>
          <MessageSquare size={15} />
        </button>
      )}
    </div>
  )
}
