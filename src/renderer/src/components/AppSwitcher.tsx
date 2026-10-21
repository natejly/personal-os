import { Inbox } from '../icons'
import { useStore } from '../store'
import { inboxBadge } from '../lib/inboxBadge'
import TitleBarSlot from './TitleBarSlot'

/** The last thing in every title bar: the extensions' `titlebar.right` items, the agent inbox (with its unread count), then `last`. */
export default function AppSwitcher({ last }: { last?: React.ReactNode } = {}): JSX.Element {
  const unread = useStore((s) => inboxBadge(s.agentInbox))
  const openInbox = useStore((s) => s.openInbox)
  /** Only worth a title-bar icon while there is something in it; otherwise the palette and the Jobs row reach it. */
  const hasItems = useStore((s) => !!s.agentInbox && s.agentInbox.counts.needs_you + s.agentInbox.counts.runs > 0)
  return (
    <div className="app-switcher no-drag">
      <TitleBarSlot side="right" />
      {hasItems && <button className="icon-btn inbox-btn" title={unread ? `Agent inbox (${unread} new)` : 'Agent inbox'}
        aria-label={unread ? `Agent inbox, ${unread} new` : 'Agent inbox'} onClick={openInbox}>
        <Inbox size={15} />
        {unread > 0 && <span className="inbox-btn-count" aria-hidden="true">{unread > 99 ? '99+' : unread}</span>}
      </button>}
      {last}
    </div>
  )
}
