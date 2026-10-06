import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useStore } from '../store'
import type { Message } from '@shared/types'

/** ‹ i/n › over the answers a regenerate kept for the last turn. Renders nothing for a single answer. */
export default function VariantSwitcher({ conversationId, message }: { conversationId?: string; message: Message }): JSX.Element | null {
  const activate = useStore((s) => s.activateVariant)
  const ids = message.variants ?? []
  if (!conversationId || ids.length < 2) return null
  const i = Math.max(0, ids.indexOf(message.id))
  const go = (to: number): void => void activate(conversationId, ids[to])
  return (
    <span className="variant-switcher" role="group" aria-label="Answer versions">
      <button className="icon-btn" aria-label="Previous answer" disabled={i === 0} onClick={() => go(i - 1)}><ChevronLeft size={13} /></button>
      <span aria-live="polite">{i + 1}/{ids.length}</span>
      <button className="icon-btn" aria-label="Next answer" disabled={i === ids.length - 1} onClick={() => go(i + 1)}><ChevronRight size={13} /></button>
    </span>
  )
}
