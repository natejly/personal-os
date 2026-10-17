import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, X } from '../icons'
import type { FullDesk } from '@shared/types'
import DeskFiles from './DeskFiles'
import DeskChanges from './DeskChanges'
import DeskReview from './DeskReview'
import { ChatFilesList } from './ChatFilesPanel'
import { useStore } from '../store'
import ContextDrawer from './ContextDrawer'
import TracesPane from './TracesPane'
import WorkersPanel from './WorkersPanel'
import ProgressCard from './ProgressCard'
import { useChatFileCount } from '../lib/useChatFiles'
import { CORE, registerSlot, type SidePanelTabProps } from '../extensions/host'
import { useSlot } from '../extensions/slot'

// The cards other than Terminal are `sidepanel.tab` slot items. Terminal owns the panel's attach state, so it stays here, last.

const ContextTab = ({ conversationId }: SidePanelTabProps): JSX.Element => <ContextDrawer conversationId={conversationId} embedded />
const FilesTab = ({ conversationId, desk }: SidePanelTabProps): JSX.Element => (desk
  ? <><ChatFilesList conversationId={conversationId ?? ''} layout="above" /><h4 className="cf-ws">Workspace</h4><DeskFiles desk={desk} /></>
  : <ChatFilesList conversationId={conversationId ?? ''} />)
const ChangesTab = ({ conversationId, desk }: SidePanelTabProps): JSX.Element => <div className="desk-pane scroll"><DeskChanges desk={desk ?? undefined} conversationId={conversationId ?? ''} /></div>
const ReviewTab = ({ desk }: SidePanelTabProps): JSX.Element | null => (desk ? <div className="desk-pane scroll"><DeskReview desk={desk} /></div> : null)
const TracesTab = ({ conversationId }: SidePanelTabProps): JSX.Element => <TracesPane conversationId={conversationId} />

registerSlot(CORE, 'sidepanel.tab', { id: 'context', order: 10, label: 'Context', Component: ContextTab })
registerSlot(CORE, 'sidepanel.tab', { id: 'files', order: 20, label: 'Files', Component: FilesTab, shown: (p) => !!p.conversationId })
registerSlot(CORE, 'sidepanel.tab', {
  id: 'changes', order: 30, label: 'Changes', Component: ChangesTab,
  shown: (p) => !!p.conversationId && (p.desk ? p.desk.runs.length > 0 : p.fileCount > 0)
})
registerSlot(CORE, 'sidepanel.tab', {
  id: 'review', order: 40, label: 'Review', Component: ReviewTab,
  shown: (p) => !!p.desk && (p.desk.outputs.length > 0 || p.desk.status === 'review'), count: (p) => p.desk?.outputs.length ?? 0
})
registerSlot(CORE, 'sidepanel.tab', { id: 'traces', order: 50, label: 'Traces', Component: TracesTab, shown: (p) => !!p.conversationId })

/**
 * The workspace of a chat, a column of floating cards over the transcript's right edge, each collapsing to its header: Files and Changes for every chat, and Review once it has a desk (a chat
 * working autonomously), which comes forward when the desk asks for it. Traces lists the chat's runs. Terminal (the coding extension's card) shows in every chat with an id when the app can host terminals (running shell jobs, the chat's coding sessions and their terminals); opening a terminal brings it forward. A running checklist shows as the compact progress card
 * above the composer (ProgressCard) and workers as the worker list above the composer. Context starts open. The step card and the workers list lead the column (ChatView leaves them off the composer while it is open). Changes shows once the chat has files or its desk has runs, Review once the desk has outputs or is in review. Without a desk, Files is the chat's
 * own list. Context (the store's `contextOpen`) is always there and wins while requested (it expands); a draft chat with no id has only that card. Requests (context, a desk tab, a terminal, a desk in review) expand their card and scroll it into view.
 */
export default function DeskPanel({ desk, conversationId, onClose }: { desk?: FullDesk | null; conversationId?: string; onClose: () => void }): JSX.Element {
  const id = conversationId ?? '' // '' only on a draft chat, where just the Context card shows
  const [open, setOpen] = useState<Record<string, boolean>>({ context: true, review: desk?.status === 'review' })
  const panel = useRef<HTMLElement>(null)
  const refs = useRef<Record<string, HTMLElement | null>>({})
  const expand = (k: string): void => { setOpen((o) => ({ ...o, [k]: true })); setTimeout(() => refs.current[k]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 50) }
  useEffect(() => { if (desk?.status === 'review') expand('review') }, [desk?.id, desk?.status])
  const contextOpen = useStore((s) => s.contextOpen)
  const fileCount = useChatFileCount(id)
  const tracesRequest = useStore((s) => s.deskTabRequest?.n)
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
  useEffect(() => { const r = useStore.getState().deskTabRequest; if (r && Date.now() - r.at < 3000) { useStore.setState({ contextOpen: false, deskPanel: true }); expand(r.tab) } }, [tracesRequest])
  useEffect(() => {
    const esc = (e: KeyboardEvent): void => { if (e.key === 'Escape' && !e.defaultPrevented && !(e.target as HTMLElement | null)?.closest?.('.xterm, .term-window, input, textarea, [contenteditable]')) onClose() }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onClose])
  const tabProps: SidePanelTabProps = { conversationId, desk, fileCount }
  const slotTabs = useSlot('sidepanel.tab').filter((t) => !t.shown || t.shown(tabProps))
  const tabs = slotTabs.map((t) => ({ id: t.id, label: t.label, count: t.count?.(tabProps), body: <t.Component {...tabProps} /> }))
  return (
    <aside ref={panel} className="desk-panel" aria-label="Workspace">
      <button className="icon-btn ghost desk-close" title="Close" aria-label="Close workspace" onClick={onClose}><X size={14} /></button>
      <ProgressCard conversationId={conversationId} desk={desk} />
      <WorkersPanel conversationId={conversationId} />
      {tabs.map((t) => {
        return (
          <section key={t.id} ref={(el) => { refs.current[t.id] = el }} className="desk-card">
            <button data-testid={`desk-tab-${t.id}`} className={`desk-card-head${open[t.id] ? ' active' : ''}`} aria-expanded={!!open[t.id]} onClick={() => setOpen((o) => ({ ...o, [t.id]: !o[t.id] }))}>
              {t.label}
              {!!t.count && <span className="count">{t.count}</span>}
              {open[t.id] ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            </button>
            {open[t.id] && <div className={`desk-card-body`}>{t.body}</div>}
          </section>
        )
      })}
      {slotTabs.map((t) => t.Overlay && <t.Overlay key={t.id} {...tabProps} />)}
    </aside>
  )
}
