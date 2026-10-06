import { createContext, useContext } from 'react'
import { PanelRight } from 'lucide-react'
import type { ShowItem } from '@shared/types'
import { useStore } from '../store'
import { fromFence } from '../lib/showPanel'

/** The conversation a rendered reply belongs to, so a fenced block in it can be promoted to that chat's side panel. */
export const ShowCtx = createContext<string | null>(null)

/** "Open in panel" on a fenced block's header. Nothing outside a chat (a doc preview) gets the button. */
export default function OpenInPanel({ kind, source }: { kind: Exclude<ShowItem['kind'], 'file'>; source: string }): JSX.Element | null {
  const conversationId = useContext(ShowCtx)
  const openShow = useStore((s) => s.openShow)
  if (!conversationId) return null
  return (
    <button className="icon-btn ghost" title="Open in side panel" aria-label="Open in side panel" onClick={() => openShow(conversationId, fromFence(kind, source))}>
      <PanelRight size={12} />
    </button>
  )
}
