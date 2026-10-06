import { RefreshCw } from 'lucide-react'
import { useStore } from '../store'
import VariantSwitcher from './VariantSwitcher'
import { followupsFor } from '../lib/followups'
import { insertIntoComposer } from '../lib/composerInsert'
import type { Message } from '@shared/types'

/** Under the last message of a chat: Regenerate (plus the answer switcher) after a reply, Retry after a message nobody answered. */
export default function RegenRow({ conversationId, last, streaming }: { conversationId?: string; last: Message | undefined; streaming: boolean }): JSX.Element | null {
  const regenerate = useStore((s) => s.regenerate)
  const send = useStore((s) => s.send)
  const runError = useStore((s) => (conversationId ? s.sessions[conversationId]?.runError : null) ?? null)
  const enabled = useStore((s) => s.settings.followUps !== false)
  if (streaming || !last) return null
  const chips = last.role === 'assistant' ? followupsFor([last], { live: streaming, enabled }) : []
  if (last.role === 'user') {
    return (
      <div className="run-error-row" role="alert">
        <span>{runError?.message ?? 'No reply was produced for this message.'}</span>
        <button className="ghost-btn" onClick={() => void regenerate(conversationId)}><RefreshCw size={13} /> Retry</button>
      </div>
    )
  }
  if (last.role !== 'assistant') return null
  return (
    <>
      <div className="regen-row">
        <button className="ghost-btn" onClick={() => void regenerate(conversationId)}><RefreshCw size={13} /> Regenerate</button>
        <VariantSwitcher conversationId={conversationId} message={last} />
      </div>
      {chips.length > 0 && (
        <div className="regen-row followup-chips" role="group" aria-label="Follow-up suggestions">
          {chips.map((t) => (
            <button key={t} className="ghost-btn" title="Click to edit, Shift-click to send"
              onClick={(e) => (e.shiftKey ? void send(t, conversationId) : insertIntoComposer(t, { conversationId }))}>{t}</button>
          ))}
        </div>
      )}
    </>
  )
}
