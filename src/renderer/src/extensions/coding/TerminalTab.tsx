import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import type { SidePanelTabProps } from '../host'
import TerminalPane, { modeFor } from './TerminalPane'
import TerminalWindow from './TerminalWindow'
import { useCoding } from './store'

/** The Terminal card: running commands, the chat's coding sessions and the shells agents opened. */
export const TerminalTab = ({ conversationId }: SidePanelTabProps): JSX.Element => <TerminalPane conversationId={conversationId} />

/**
 * What the card opens over the chat: the attached terminal's large window. It is drawn whenever the card is listed,
 * so collapsing the card does not close the window. A request to show a coding session (a tool card's "Show coding
 * session") attaches it here, once, while the request is fresh.
 */
export function TerminalOverlay(): JSX.Element | null {
  const attached = useCoding((s) => s.attached)
  const request = useCoding((s) => s.terminalRequest?.n)
  const detach = useCoding((s) => s.detach)
  useEffect(() => { void useCoding.getState().refresh() }, [])
  useEffect(() => {
    const r = useCoding.getState().terminalRequest
    if (!r || Date.now() - r.at > 3000) return  // an old request must not pull a freshly opened panel onto a terminal
    const c = useCoding.getState().sessions[r.sessionId]
    useCoding.getState().attach(r.sessionId, r.mode ?? (c ? modeFor(c) : 'attach'))
  }, [request])
  // The panel is in the document only after its first render: the window goes over the chat that holds it.
  const [parent, setParent] = useState<Element | null>(null)
  useEffect(() => setParent(document.querySelector('.desk-panel')?.parentElement ?? null), [])
  return attached && parent ? createPortal(<TerminalWindow key={`${attached.id}:${attached.mode}`} sessionId={attached.id} mode={attached.mode} onClose={detach} />, parent) : null
}
