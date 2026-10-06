import { useMemo } from 'react'
import { Pencil, Send, X } from 'lucide-react'
import { useDraft } from '../lib/drafts'
import { parseQueue, queueKey, removeQueued, updateQueue, type QueuedItem } from '../lib/followQueue'

interface QueueTrayProps {
  conversationId: string
  /** The run is still answering: the head of the queue goes out on its final `done`. */
  busy: boolean
  /** Edit: the item has left the queue and its text belongs in the composer. */
  onEdit: (text: string) => void
  /** Send now: a steer while busy, an ordinary send otherwise. The composer owns the card confirm. */
  onSendNow: (item: QueuedItem) => void
  /** Resume a paused queue; when idle it also sends the head now. */
  onResume: () => void
}

/** The follow-ups waiting on this chat's reply, above its composer. Renders nothing when there are none. */
export default function QueueTray({ conversationId, busy, onEdit, onSendNow, onResume }: QueueTrayProps): JSX.Element | null {
  const [raw] = useDraft(queueKey(conversationId))
  const q = useMemo(() => parseQueue(raw), [raw])
  if (!q.items.length) return null
  // Idle with items left (stopped, failed, or a reload after the run ended) is waiting on the user too.
  const waiting = q.paused || !busy
  return (
    <div className="queue-tray" aria-label="Queued follow-ups">
      <div className="queue-tray-head">
        <span>{waiting ? 'Paused' : 'Queued: runs after this reply'}</span>
        {waiting && <button className="link" onClick={onResume}>Resume</button>}
      </div>
      {q.items.map((item) => (
        <div className="queue-item" key={item.id}>
          <span className="queue-text" title={item.text}>{item.text}</span>
          <button className="icon-btn" title="Edit" aria-label="Edit" onClick={() => {
            updateQueue(conversationId, (cur) => removeQueued(cur, item.id))
            onEdit(item.text)
          }}><Pencil size={13} /></button>
          <button className="icon-btn" title="Remove" aria-label="Remove" onClick={() => updateQueue(conversationId, (cur) => removeQueued(cur, item.id))}><X size={13} /></button>
          <button className="icon-btn" title={busy ? 'Send now (steers the reply)' : 'Send now'} aria-label="Send now" onClick={() => onSendNow(item)}><Send size={13} /></button>
        </div>
      ))}
    </div>
  )
}
