import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import type { FullDesk } from '@shared/types'
import ResizeHandle from './ResizeHandle'
import DeskFiles from './DeskFiles'
import DeskChanges from './DeskChanges'
import DeskReview from './DeskReview'

type Tab = 'files' | 'changes' | 'review'
const TABS: { key: Tab; label: string }[] = [{ key: 'files', label: 'Files' }, { key: 'changes', label: 'Changes' }, { key: 'review', label: 'Review' }]

/** The workspace of a chat working autonomously, beside the transcript. Review comes forward when the desk asks for it. */
export default function DeskPanel({ desk, onClose }: { desk: FullDesk; onClose: () => void }): JSX.Element {
  const [tab, setTab] = useState<Tab>(desk.status === 'review' ? 'review' : 'files')
  useEffect(() => { if (desk.status === 'review') setTab('review') }, [desk.id, desk.status])
  return (
    <aside className="desk-panel" aria-label="Workspace">
      <ResizeHandle id="desk-panel-w" defaultSize={460} min={320} max={900} grows="left" onCollapse={onClose} label="Workspace panel width" className="at-left" />
      <div className="desk-tabs tabs">
        {TABS.map((t) => (
          <button key={t.key} className={tab === t.key ? 'active' : ''} onClick={() => setTab(t.key)}>
            {t.label}
            {t.key === 'review' && desk.outputs.length > 0 && <span className="count">{desk.outputs.length}</span>}
          </button>
        ))}
        <span className="spacer" />
        <button className="icon-btn ghost" title="Close" aria-label="Close the workspace panel" onClick={onClose}><X size={14} /></button>
      </div>
      {tab === 'files' && <DeskFiles desk={desk} />}
      {tab === 'changes' && <div className="desk-pane scroll"><p className="muted small">What each turn changed in the workspace, with Undo.</p><DeskChanges desk={desk} /></div>}
      {tab === 'review' && <div className="desk-pane scroll"><DeskReview desk={desk} /></div>}
    </aside>
  )
}
