import { useEffect, useRef, useState } from 'react'
import { Upload, Trash2, FileText, PanelLeftOpen, X } from 'lucide-react'
import { useStore, type Scope } from '../store'
import { api } from '../lib/api'
import type { Document } from '@shared/types'
import ProjectChip from './ProjectChip'
import ScopeSelect from './ScopeSelect'

const fmtSize = (n: number): string => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`)
const ACCEPT = '.txt,.md,.markdown,.pdf,.docx,.csv,.json,.yaml,.yml,.py,.ts,.tsx,.js,.html,.css,.log,.rst,.toml'

export default function DocumentsView({ projectId, embedded = false }: { projectId?: string; embedded?: boolean }): JSX.Element {
  const documents = useStore((s) => s.documents)
  const libraryScope = useStore((s) => s.libraryScope)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const { uploadDocuments, deleteDocument, toggleSidebar, setLibraryScope, loadScope } = useStore()
  const scope: Scope = embedded ? projectId! : libraryScope
  const fileRef = useRef<HTMLInputElement>(null)
  const [drag, setDrag] = useState(false)
  const [open, setOpen] = useState<Document | null>(null)

  useEffect(() => { void loadScope(scope) }, [scope, loadScope])
  const targetProject = scope === 'all' || scope === 'personal' ? null : scope
  const view = async (d: Document): Promise<void> => setOpen(await api.documents.get(d.id))

  const uploadBtn = (
    <>
      <input id={embedded ? 'doc-upload-input-project' : 'doc-upload-input'} ref={fileRef} type="file" multiple hidden accept={ACCEPT}
        onChange={(e) => { if (e.target.files?.length) void uploadDocuments(e.target.files, targetProject); e.target.value = '' }} />
      <button className="primary-btn" onClick={() => fileRef.current?.click()}><Upload size={14} /> Upload{targetProject ? ' to project' : ''}</button>
    </>
  )

  const body = (
    <div className={`page-body ${drag ? 'dragging' : ''}`} onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) void uploadDocuments(e.dataTransfer.files, targetProject) }}>
      {embedded && <div className="add-row">{uploadBtn}<span className="muted small">Knowledge for this project: .txt, .md, .pdf, .docx and code files. Drop files anywhere here.</span></div>}
      {!embedded && <p className="muted small">Supports .txt, .md, .pdf, .docx and common code/text files. Documents are chunked and full-text indexed; the best matching excerpts are pulled into chats automatically. Personal documents are available everywhere; project documents only inside that project. Drop files anywhere here.</p>}
      {documents.length === 0 && <p className="empty-hint big">No documents here yet.</p>}
      <div className="doc-grid">
        {documents.map((d) => (
          <div key={d.id} className="doc-card" onClick={() => void view(d)}>
            <div className="doc-head">
              <FileText size={16} />
              <span className="doc-name" title={d.name}>{d.name}</span>
              <button className="icon-btn ghost danger" onClick={(e) => { e.stopPropagation(); void deleteDocument(d.id) }}><Trash2 size={13} /></button>
            </div>
            <p className="doc-preview">{d.preview || '(no text extracted)'}</p>
            <div className="doc-meta">{scope === 'all' && <ProjectChip projectId={d.project_id} showPersonal />} {fmtSize(d.size)} · {d.chunk_count} chunks · {new Date(d.created_at * 1000).toLocaleDateString()}</div>
          </div>
        ))}
      </div>
      {open && (
        <div className="modal-backdrop" onMouseDown={() => setOpen(null)}>
          <div className="modal wide" onMouseDown={(e) => e.stopPropagation()}>
            <header><h2>{open.name}</h2><button className="icon-btn" onClick={() => setOpen(null)}><X size={16} /></button></header>
            <pre className="doc-text">{open.text}</pre>
          </div>
        </div>
      )}
    </div>
  )
  if (embedded) return body

  return (
    <main className="page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><FileText size={16} /> Documents</h2>
        <div className="no-drag header-right">
          <ScopeSelect value={libraryScope} onChange={(s) => void setLibraryScope(s)} />
          {uploadBtn}
        </div>
      </header>
      {body}
    </main>
  )
}
