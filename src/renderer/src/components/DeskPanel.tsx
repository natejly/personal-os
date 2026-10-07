import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import type { FullDesk } from '@shared/types'
import ResizeHandle from './ResizeHandle'
import DeskFiles from './DeskFiles'
import DeskChanges from './DeskChanges'
import DeskReview from './DeskReview'
import { ChatFilesList } from './ChatFilesPanel'
import PlanPanel from './PlanPanel'
import DeskPlan from './DeskPlan'
import WorkersPanel from './WorkersPanel'
import { useStore, useWorkers } from '../store'

type Tab = 'files' | 'changes' | 'checklist' | 'workers' | 'review'
const TABS: { key: Tab; label: string }[] = [{ key: 'files', label: 'Files' }, { key: 'changes', label: 'Changes' }, { key: 'checklist', label: 'Checklist' }, { key: 'workers', label: 'Workers' }, { key: 'review', label: 'Review' }]

/**
 * The side panel of a chat, beside the transcript: Files and Changes for every chat; Checklist once the agent keeps one,
 * Workers once it has started any, and Review once it has a desk (a chat working autonomously), which comes forward when
 * the desk asks for it. The checklist and finished workers live here, not above the composer: the main agent's own
 * progress has no status card (lib/statusChrome). Without a desk, Files is the chat's own list.
 */
export default function DeskPanel({ desk, conversationId, onClose }: { desk?: FullDesk | null; conversationId: string; onClose: () => void }): JSX.Element {
  const [picked, setTab] = useState<Tab>(desk?.status === 'review' ? 'review' : 'files')
  useEffect(() => { if (desk?.status === 'review') setTab('review') }, [desk?.id, desk?.status])
  const hasChecklist = useStore((s) => (s.plans[conversationId]?.length ?? 0) > 0) || (!!desk?.plan && desk.plan.status !== 'pending')
  const hasWorkers = useWorkers(conversationId).length > 0
  const shown = (k: Tab): boolean => k === 'files' || k === 'changes' || (k === 'checklist' && hasChecklist) || (k === 'workers' && hasWorkers) || (k === 'review' && !!desk)
  const tabs = TABS.filter((t) => shown(t.key))
  const tab = shown(picked) ? picked : 'files'
  return (
    <aside className="desk-panel" aria-label="Workspace">
      <ResizeHandle id="desk-panel-w" defaultSize={460} min={320} max={900} grows="left" onCollapse={onClose} label="Workspace panel width" className="at-left" />
      <div className="desk-tabs tabs">
        {tabs.map((t) => (
          <button key={t.key} className={tab === t.key ? 'active' : ''} onClick={() => setTab(t.key)}>
            {t.label}
            {t.key === 'review' && desk && desk.outputs.length > 0 && <span className="count">{desk.outputs.length}</span>}
          </button>
        ))}
        <span className="spacer" />
        <button className="icon-btn ghost" title="Close" aria-label="Close the workspace panel" onClick={onClose}><X size={14} /></button>
      </div>
      {tab === 'files' && (desk
        ? <><ChatFilesList conversationId={conversationId} layout="above" /><h4 className="cf-ws">Workspace</h4><DeskFiles desk={desk} /></>
        : <ChatFilesList conversationId={conversationId} />)}
      {tab === 'changes' && <div className="desk-pane scroll"><p className="muted small">{desk ? 'What each turn changed in the workspace, with Undo.' : 'What each reply changed in your folders, with Undo.'}</p><DeskChanges desk={desk ?? undefined} conversationId={conversationId} /></div>}
      {tab === 'checklist' && <div className="desk-pane scroll"><PlanPanel conversationId={conversationId} />{desk?.plan && desk.plan.status !== 'pending' && <DeskPlan desk={desk} />}</div>}
      {tab === 'workers' && <div className="desk-pane scroll"><WorkersPanel conversationId={conversationId} all /></div>}
      {tab === 'review' && desk && <div className="desk-pane scroll"><DeskReview desk={desk} /></div>}
    </aside>
  )
}
