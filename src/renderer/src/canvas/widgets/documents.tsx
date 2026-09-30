import { useEffect, useRef } from 'react'
import { FileText, Trash2, Upload } from 'lucide-react'
import type { DragKind, Document } from '@shared/types'
import { useStore, type Scope } from '../../store'
import { dragProps, useDropTarget } from '../dnd'
import type { WidgetDef, WidgetProps } from '../registry'

const ACCEPTS: DragKind[] = ['file']
const ACCEPT = '.txt,.md,.markdown,.pdf,.docx,.csv,.json,.yaml,.yml,.py,.ts,.tsx,.js,.html,.css,.log,.rst,.toml'

const fmtSize = (n: number): string => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`)
const inScope = (d: Document, s: Scope): boolean =>
  s === 'all' ? true : s === 'personal' ? d.project_id === null : d.project_id === s

function Row({ d }: { d: Document }): JSX.Element {
  const deleteDocument = useStore((s) => s.deleteDocument)
  return (
    <div className="widget-row" {...dragProps({ kind: 'document', id: d.id, label: d.name, projectId: d.project_id })}>
      <FileText size={14} />
      <div className="grow">
        <div className="widget-title" title={d.name}>{d.name}</div>
        <div className="widget-sub">{d.preview || '(no text extracted)'}</div>
      </div>
      <span className="widget-meta">{fmtSize(d.size)} · {d.chunk_count}</span>
      <button className="icon-btn danger" title="Delete" onClick={() => void deleteDocument(d.id)}><Trash2 size={13} /></button>
    </div>
  )
}

export default function DocumentsWidget({ window: win, live, onConfig }: WidgetProps): JSX.Element {
  const documents = useStore((s) => s.documents)
  const projects = useStore((s) => s.projects)
  const refreshDocuments = useStore((s) => s.refreshDocuments)
  const uploadDocuments = useStore((s) => s.uploadDocuments)
  const fileRef = useRef<HTMLInputElement>(null)
  const loaded = useRef(false)
  const scope = (typeof win.config.scope === 'string' ? win.config.scope : 'all') as Scope
  const targetProject = scope === 'all' || scope === 'personal' ? null : scope
  const drop = useDropTarget(ACCEPTS, (_p, e) => {
    if (e.dataTransfer.files.length) void uploadDocuments(e.dataTransfer.files, targetProject)
  })

  // The rows are the store's shared array, so one load per window is enough — and none at all off-screen.
  useEffect(() => {
    if (!live || loaded.current) return
    loaded.current = true
    void refreshDocuments()
  }, [live, refreshDocuments])

  if (!live) return <div className="widget"><div className="widget-empty">Documents · paused</div></div>

  const rows = documents.filter((d) => inScope(d, scope))
  return (
    <div className={drop.over ? 'widget drop-over' : 'widget'} {...drop.handlers}>
      <div className="widget-bar">
        <select className="widget-chip" title="Filter by project" value={scope} onChange={(e) => onConfig({ scope: e.target.value })}>
          <option value="all">All</option>
          <option value="personal">Personal only</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <input ref={fileRef} type="file" multiple hidden accept={ACCEPT}
          onChange={(e) => { if (e.target.files?.length) void uploadDocuments(e.target.files, targetProject); e.target.value = '' }} />
        <button className="widget-chip" title={targetProject ? 'Upload to this project' : 'Upload'} onClick={() => fileRef.current?.click()}><Upload size={11} /> Upload</button>
        <span className="spacer" />
        <span>{rows.length}</span>
      </div>
      {rows.length === 0 ? (
        <div className="widget-empty">No documents in this scope.</div>
      ) : (
        <div className="widget-scroll"><div className="widget-list">{rows.map((d) => <Row key={d.id} d={d} />)}</div></div>
      )}
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'documents',
  label: 'Documents',
  icon: <FileText size={15} />,
  defaultSize: { w: 400, h: 480 },
  minSize: { w: 280, h: 240 },
  chrome: 'full',
  accepts: ACCEPTS,
  defaultConfig: { scope: 'all' },
  Component: DocumentsWidget
}
