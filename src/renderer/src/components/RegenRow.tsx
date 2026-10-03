import { RefreshCw } from 'lucide-react'
import { useStore } from '../store'
import type { Message } from '@shared/types'

/** The Regenerate button under a finished reply. */
export default function RegenRow({ conversationId, last, streaming }: { conversationId?: string; last: Message | undefined; streaming: boolean }): JSX.Element | null {
  const regenerate = useStore((s) => s.regenerate)
  if (streaming || last?.role !== 'assistant') return null
  return (
    <div className="regen-row">
      <button className="ghost-btn" onClick={() => void regenerate(conversationId)}><RefreshCw size={13} /> Regenerate</button>
    </div>
  )
}
