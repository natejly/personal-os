import { useEffect, useState } from 'react'
import { X } from '../icons'
import type { FullDesk } from '@shared/types'
import ResizeHandle from './ResizeHandle'
import DeskFiles from './DeskFiles'
import DeskChanges from './DeskChanges'
import DeskReview from './DeskReview'
import { ChatFilesList } from './ChatFilesPanel'
import { useStore } from '../store'
import ContextDrawer from './ContextDrawer'
import { useTermAvailable } from '../lib/termRegistry'
import { termSessions } from '../lib/codingSessions'
import TerminalPane from './TerminalPane'

type Tab = 'context' | 'files' | 'changes' | 'review' | 'terminal'
const TABS: { key: Tab; label: string }[] = [{ key: 'context', label: 'Context' }, { key: 'files', label: 'Files' }, { key: 'changes', label: 'Changes' }, { key: 'review', label: 'Review' }, { key: 'terminal', label: 'Terminal' }]

/** An openTerminal request made just now; an old one must not pull a freshly opened panel onto Terminal. */
const freshTermRequest = (): boolean => Date.now() - (useStore.getState().terminalRequest?.at ?? 0) < 3000

/**
 * The side panel of a chat, beside the transcript: Files and Changes for every chat, and Review once it has a desk (a chat
 * working autonomously), which comes forward when the desk asks for it. Terminal shows when the app can host terminals and a
 * coding session exists; an openTerminal request brings it forward. A running checklist shows as the compact progress card
 * above the composer (ProgressCard) and workers as the worker list above the composer. Context is the default tab. Without a desk, Files is the chat's
 * own list. Context (the store's `contextOpen`) is always there and wins while requested; a draft chat with no id has only that tab.
 */
export default function DeskPanel({ desk, conversationId, onClose }: { desk?: FullDesk | null; conversationId?: string; onClose: () => void }): JSX.Element {
  const id = conversationId ?? '' // '' only on a draft chat, where just the Context tab shows
  const [picked, setTab] = useState<Tab>(freshTermRequest() ? 'terminal' : desk?.status === 'review' ? 'review' : 'context')
  useEffect(() => { if (desk?.status === 'review') setTab('review') }, [desk?.id, desk?.status])
  const contextOpen = useStore((s) => s.contextOpen)
  const termOk = useTermAvailable()
  const hasCoding = useStore((s) => termSessions(Object.values(s.codingSessions), conversationId).length > 0)
  const termRequest = useStore((s) => s.terminalRequest?.n)
  useEffect(() => { void useStore.getState().refreshCodingSessions() }, [])
  useEffect(() => { if (termRequest && freshTermRequest()) setTab('terminal') }, [termRequest])
  const shown = (k: Tab): boolean => k === 'context' || (!!conversationId && (k === 'files' || k === 'changes')) || (k === 'review' && !!desk) || (k === 'terminal' && termOk && hasCoding)
  const tabs = TABS.filter((t) => shown(t.key))
  const tab: Tab = contextOpen ? 'context' : shown(picked) ? picked : 'context'
  const pick = (k: Tab): void => { useStore.setState({ contextOpen: k === 'context' }); setTab(k) }
  return (
    <aside className="desk-panel" aria-label="Workspace">
      <ResizeHandle id="desk-panel-w" defaultSize={460} min={320} max={900} keep={360} overlayAt={1100} grows="left" onCollapse={onClose} label="Workspace panel width" className="at-left" />
      <div className="desk-tabs tabs">
        {tabs.map((t) => (
          <button key={t.key} data-testid={`desk-tab-${t.key}`} className={tab === t.key ? 'active' : ''} onClick={() => pick(t.key)}>
            {t.label}
            {t.key === 'review' && desk && desk.outputs.length > 0 && <span className="count">{desk.outputs.length}</span>}
          </button>
        ))}
        <span className="spacer" />
        <button className="icon-btn ghost" title="Close" aria-label="Close the workspace panel" onClick={onClose}><X size={14} /></button>
      </div>
      {tab === 'context' && <ContextDrawer conversationId={conversationId} embedded />}
      {tab === 'files' && (desk
        ? <><ChatFilesList conversationId={id} layout="above" /><h4 className="cf-ws">Workspace</h4><DeskFiles desk={desk} /></>
        : <ChatFilesList conversationId={id} />)}
      {tab === 'changes' && <div className="desk-pane scroll"><DeskChanges desk={desk ?? undefined} conversationId={id} /></div>}
      {tab === 'terminal' && <TerminalPane conversationId={conversationId} />}
      {tab === 'review' && desk && <div className="desk-pane scroll"><DeskReview desk={desk} /></div>}
    </aside>
  )
}
