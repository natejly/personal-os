import { useEffect, useRef } from 'react'
import { X } from 'lucide-react'
import { useStore } from '../store'
import { useModal } from '../lib/useModal'
import AgentInbox from './AgentInbox'

/** The agent inbox (needs-you cards, job runs, archive, scheduled tasks), opened from the title-bar Inbox icon. */
export default function InboxModal(): JSX.Element {
  const close = useStore((s) => s.closeInbox)
  const m = useModal(close)
  // Following any inbox link (a chat, a desk, a view) leaves the inbox behind.
  const where = useStore((s) => `${s.view}|${s.focusedConversationId}`)
  const first = useRef(where)
  useEffect(() => { if (where !== first.current) close() }, [where, close])
  return (
    <div className="modal-backdrop" {...m.backdrop}>
      <div className="modal inbox-modal" {...m.modal} aria-label="Agent inbox" aria-labelledby={undefined}>
        <button className="icon-btn inbox-modal-close" aria-label="Close inbox" onClick={close}><X size={15} /></button>
        <AgentInbox />
      </div>
    </div>
  )
}
