import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, X } from '../icons'
import type { FullDesk } from '@shared/types'
import DeskFiles from './DeskFiles'
import DeskChanges from './DeskChanges'
import DeskReview from './DeskReview'
import { ChatFilesList } from './ChatFilesPanel'
import { useStore } from '../store'
import ContextDrawer from './ContextDrawer'
import { useTermAvailable } from '../lib/termRegistry'
import TerminalPane from './TerminalPane'
import TracesPane from './TracesPane'

type Tab = 'context' | 'files' | 'changes' | 'review' | 'traces' | 'terminal'
const TABS: { key: Tab; label: string }[] = [{ key: 'context', label: 'Context' }, { key: 'files', label: 'Files' }, { key: 'changes', label: 'Changes' }, { key: 'review', label: 'Review' }, { key: 'traces', label: 'Traces' }, { key: 'terminal', label: 'Terminal' }]

/** An openTerminal request made just now; an old one must not pull a freshly opened panel onto Terminal. */
const freshTermRequest = (): boolean => Date.now() - (useStore.getState().terminalRequest?.at ?? 0) < 3000

/**
 * The workspace of a chat, a column of floating cards over the transcript's right edge, each collapsing to its header: Files and Changes for every chat, and Review once it has a desk (a chat
 * working autonomously), which comes forward when the desk asks for it. Traces lists the chat's runs. Terminal shows in every chat with an id when the app can host terminals (running shell jobs, the chat's coding sessions and their terminals); an openTerminal request brings it forward. A running checklist shows as the compact progress card
 * above the composer (ProgressCard) and workers as the worker list above the composer. Context starts open. Without a desk, Files is the chat's
 * own list. Context (the store's `contextOpen`) is always there and wins while requested (it expands); a draft chat with no id has only that card. Requests (context, a desk tab, a terminal, a desk in review) expand their card and scroll it into view.
 */
export default function DeskPanel({ desk, conversationId, onClose }: { desk?: FullDesk | null; conversationId?: string; onClose: () => void }): JSX.Element {
  const id = conversationId ?? '' // '' only on a draft chat, where just the Context card shows
  const [open, setOpen] = useState<Partial<Record<Tab, boolean>>>({ context: true, terminal: freshTermRequest(), review: desk?.status === 'review' })
  const panel = useRef<HTMLElement>(null)
  const refs = useRef<Partial<Record<Tab, HTMLElement | null>>>({})
  const expand = (k: Tab): void => { setOpen((o) => ({ ...o, [k]: true })); setTimeout(() => refs.current[k]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 50) }
  useEffect(() => { if (desk?.status === 'review') expand('review') }, [desk?.id, desk?.status])
  const contextOpen = useStore((s) => s.contextOpen)
  const termOk = useTermAvailable()
  const tracesRequest = useStore((s) => s.deskTabRequest?.n)
  const termRequest = useStore((s) => s.terminalRequest?.n)
  useEffect(() => { void useStore.getState().refreshCodingSessions() }, [])
  // Stop at the bottom of the transcript, so the column never covers the checklist, workers or composer under it, whatever their height.
  useEffect(() => {
    const body = panel.current?.parentElement
    const msgs = body?.querySelector<HTMLElement>('.chat-main .messages')
    if (!body || !msgs) return
    const fit = (): void => { if (panel.current) panel.current.style.bottom = `${Math.max(0, body.getBoundingClientRect().bottom - msgs.getBoundingClientRect().bottom) + 8}px` }
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(msgs); ro.observe(body)
    return () => ro.disconnect()
  }, [])
  useEffect(() => { if (contextOpen) expand('context') }, [contextOpen])
  useEffect(() => { if (termRequest && freshTermRequest()) expand('terminal') }, [termRequest])
  useEffect(() => { const r = useStore.getState().deskTabRequest; if (r && Date.now() - r.at < 3000) { useStore.setState({ contextOpen: false }); expand(r.tab) } }, [tracesRequest])
  useEffect(() => {
    const esc = (e: KeyboardEvent): void => { if (e.key === 'Escape' && !e.defaultPrevented && !(e.target as HTMLElement | null)?.closest?.('.xterm, input, textarea, [contenteditable]')) onClose() }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onClose])
  const shown = (k: Tab): boolean => k === 'context' || (!!conversationId && (k === 'files' || k === 'changes')) || (k === 'review' && !!desk) || (k === 'traces' && !!conversationId) || (k === 'terminal' && termOk && !!conversationId)
  const content = (k: Tab): JSX.Element | null => {
    switch (k) {
      case 'context': return <ContextDrawer conversationId={conversationId} embedded />
      case 'files': return desk
        ? <><ChatFilesList conversationId={id} layout="above" /><h4 className="cf-ws">Workspace</h4><DeskFiles desk={desk} /></>
        : <ChatFilesList conversationId={id} />
      case 'changes': return <div className="desk-pane scroll"><DeskChanges desk={desk ?? undefined} conversationId={id} /></div>
      case 'terminal': return <TerminalPane conversationId={conversationId} />
      case 'traces': return <TracesPane conversationId={conversationId} />
      case 'review': return desk ? <div className="desk-pane scroll"><DeskReview desk={desk} /></div> : null
    }
  }
  return (
    <aside ref={panel} className="desk-panel" aria-label="Workspace">
      <button className="icon-btn ghost desk-close" title="Close" aria-label="Close workspace" onClick={onClose}><X size={14} /></button>
      {TABS.filter((t) => shown(t.key)).map((t) => (
        <section key={t.key} ref={(el) => { refs.current[t.key] = el }} className="desk-card">
          <button data-testid={`desk-tab-${t.key}`} className={`desk-card-head${open[t.key] ? ' active' : ''}`} aria-expanded={!!open[t.key]} onClick={() => setOpen((o) => ({ ...o, [t.key]: !o[t.key] }))}>
            {open[t.key] ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            {t.label}
            {t.key === 'review' && desk && desk.outputs.length > 0 && <span className="count">{desk.outputs.length}</span>}
          </button>
          {open[t.key] && <div className={`desk-card-body${t.key === 'terminal' ? ' term' : ''}`}>{content(t.key)}</div>}
        </section>
      ))}
    </aside>
  )
}
