import { useEffect, useRef, useState } from 'react'
import { X } from '../icons'
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
import SidePanel from './SidePanel'
import { useChatFileCount } from '../lib/useChatFiles'
import { CORE, registerSlot, type SidePanelTabProps } from '../extensions/host'
import { useSlot } from '../extensions/slot'

// The tabs other than Terminal are `sidepanel.tab` slot items. Terminal owns the panel's attach state, so it stays in the coding extension, last.

const ContextTab = ({ conversationId }: SidePanelTabProps): JSX.Element => <ContextDrawer conversationId={conversationId} />
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
 * The side panel of a chat: a resizable column (SidePanel) with a tab strip over one scrolling body. Tabs are the
 * `sidepanel.tab` slot items in slot order: Context, Files, Changes (once the chat has files or its desk has runs),
 * Review (once a desk has outputs or is in review), Traces, then extension tabs such as Terminal. A draft chat with
 * no id has only Context. The running checklist (ProgressCard) and the workers list lead the panel above the strip.
 * It opens on Context, on Review when its desk is in review, and on the tab a request names (the store's
 * `deskTabRequest`: traces, terminal, files, review); the last tab chosen is kept while the panel stays mounted.
 * Escape closes it.
 */
export default function DeskPanel({ desk, conversationId, onClose }: { desk?: FullDesk | null; conversationId?: string; onClose: () => void }): JSX.Element {
  const id = conversationId ?? '' // '' only on a draft chat, where just the Context tab shows
  const [active, setActive] = useState<string>(desk?.status === 'review' ? 'review' : 'context')
  const strip = useRef<HTMLDivElement>(null)
  useEffect(() => { if (desk?.status === 'review') setActive('review') }, [desk?.id, desk?.status])
  const contextOpen = useStore((s) => s.contextOpen)
  const fileCount = useChatFileCount(id)
  const tracesRequest = useStore((s) => s.deskTabRequest?.n)
  useEffect(() => { if (contextOpen) setActive('context') }, [contextOpen])
  useEffect(() => { const r = useStore.getState().deskTabRequest; if (r && Date.now() - r.at < 3000) { useStore.setState({ contextOpen: false, deskPanel: true }); setActive(r.tab) } }, [tracesRequest])
  useEffect(() => {
    const esc = (e: KeyboardEvent): void => { if (e.key === 'Escape' && !e.defaultPrevented && !(e.target as HTMLElement | null)?.closest?.('.xterm, .term-window, input, textarea, [contenteditable]')) onClose() }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onClose])
  const tabProps: SidePanelTabProps = { conversationId, desk, fileCount }
  const slotTabs = useSlot('sidepanel.tab').filter((t) => !t.shown || t.shown(tabProps))
  const current = slotTabs.find((t) => t.id === active) ?? slotTabs[0]

  const onKeys = (e: React.KeyboardEvent): void => {
    const i = slotTabs.findIndex((t) => t.id === current?.id)
    const to = e.key === 'ArrowRight' ? (i + 1) % slotTabs.length : e.key === 'ArrowLeft' ? (i - 1 + slotTabs.length) % slotTabs.length : e.key === 'Home' ? 0 : e.key === 'End' ? slotTabs.length - 1 : -1
    if (to < 0 || !slotTabs.length) return
    e.preventDefault()
    setActive(slotTabs[to].id)
    strip.current?.querySelector<HTMLElement>(`[data-tab="${slotTabs[to].id}"]`)?.focus()
  }

  return (
    <SidePanel label="Workspace" className="desk-panel" onClose={onClose}
      header={
        <div className="side-panel-lead">
          <ProgressCard conversationId={conversationId} desk={desk} />
          <WorkersPanel conversationId={conversationId} />
        </div>
      }>
      <div className="side-tabs-row">
      <div ref={strip} className="side-tabs" role="tablist" aria-label="Workspace sections" onKeyDown={onKeys}>
        {slotTabs.map((t) => {
          const count = t.count?.(tabProps)
          const on = t.id === current?.id
          return (
            <button key={t.id} type="button" role="tab" id={`side-tab-${t.id}`} data-tab={t.id} data-testid={`desk-tab-${t.id}`} className={`side-tab${on ? ' active' : ''}`}
              aria-selected={on} aria-controls="side-tabpanel" tabIndex={on ? 0 : -1} onClick={() => setActive(t.id)}>
              {t.label}
              {!!count && <span className="count">{count}</span>}
            </button>
          )
        })}
      </div>
      <button className="icon-btn ghost desk-close" title="Close" aria-label="Close workspace" onClick={onClose}><X size={14} /></button>
      </div>
      {current && (
        <div key={current.id} className="side-panel-body" role="tabpanel" id="side-tabpanel" aria-labelledby={`side-tab-${current.id}`}>
          <current.Component {...tabProps} />
        </div>
      )}
      {slotTabs.map((t) => t.Overlay && <t.Overlay key={t.id} {...tabProps} />)}
    </SidePanel>
  )
}
