import { useEffect, useRef, useState } from 'react'
import { Upload, Trash2, FileText, PanelLeftOpen, X } from 'lucide-react'
import { useStore, type Scope } from '../store'
import { api } from '../lib/api'
import type { Document } from '@shared/types'
import ProjectChip from './ProjectChip'
import ScopeSelect from './ScopeSelect'
import SendToSpace from './SendToSpace'
import { lines, usePageContext } from '../lib/pageContext'
import AppSwitcher from './AppSwitcher'

const fmtSize = (n: number): string => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`)
const ACCEPT = '.txt,.md,.markdown,.pdf,.docx,.csv,.json,.yaml,.yml,.py,.ts,.tsx,.js,.html,.css,.log,.rst,.toml'

export default function DocumentsView({ projectId, embedded = false }: { projectId?: string; embedded?: boolean }): JSX.Element {
  const documents = useStore((s) => s.documents)
  const libraryScope = useStore((s) => s.libraryScope)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const { uploadDocuments, deleteDocument, toggleSidebar, setLibraryScope, loadScope } = useStore()
  // Embedded without a project (Settings → Knowledge base) it follows the library scope, like the page did.
  const scope: Scope = projectId ?? libraryScope
  const fileRef = useRef<HTMLInputElement>(null)
  const [drag, setDrag] = useState(false)
  const [open, setOpen] = useState<Document | null>(null)

  useEffect(() => { void loadScope(scope) }, [scope, loadScope])
  const targetProject = scope === 'all' || scope === 'personal' ? null : scope
  const view = async (d: Document): Promise<void> => setOpen(await api.documents.get(d.id))

  const uploadBtn = (
    <>
      <input id={projectId ? 'doc-upload-input-project' : 'doc-upload-input'} ref={fileRef} type="file" multiple hidden accept={ACCEPT}
        onChange={(e) => { if (e.target.files?.length) void uploadDocuments(e.target.files, targetProject); e.target.value = '' }} />
      <button className="primary-btn" onClick={() => fileRef.current?.click()}><Upload size={14} /> Upload{targetProject ? ' to project' : ''}</button>
    </>
  )

  const body = (
    <div className={`page-body ${drag ? 'dragging' : ''}`} onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
      title="Drop files to upload"
      onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) void uploadDocuments(e.dataTransfer.files, targetProject) }}>
      {embedded && <div className="add-row">{uploadBtn}<span className="muted small">{projectId ? 'Knowledge for this project: ' : ''}.txt, .md, .pdf, .docx and code files. Drop files anywhere here.</span></div>}
      {!embedded && <p className="muted small">Supports .txt, .md, .pdf, .docx and common code/text files. Documents are chunked and full-text indexed; the best matching excerpts are pulled into chats automatically. Personal documents are available everywhere; project documents only inside that project. Drop files anywhere here.</p>}
      {documents.length === 0 && (
        <div className="empty-state">
          <FileText size={28} />
          <h2>No documents yet</h2>
          <p>Upload files, or drop them anywhere on this page.</p>
          <button className="primary-btn" onClick={() => fileRef.current?.click()}><Upload size={14} /> Upload{targetProject ? ' to project' : ''}</button>
        </div>
      )}
      <div className="doc-grid">
        {documents.map((d) => (
          <div key={d.id} className="doc-card" onClick={() => void view(d)}>
            <div className="doc-head">
              <FileText size={16} />
              <span className="doc-name" title={d.name}>{d.name}</span>
              <button className="icon-btn ghost danger" aria-label={`Delete ${d.name}`} onClick={(e) => { e.stopPropagation(); void deleteDocument(d.id) }}><Trash2 size={13} /></button>
            </div>
            <p className="doc-preview">{d.preview || '(no text extracted)'}</p>
            <div className="doc-meta">{scope === 'all' && <ProjectChip projectId={d.project_id} showPersonal />} {fmtSize(d.size)} · {d.chunk_count} chunks · {new Date(d.created_at * 1000).toLocaleDateString()}</div>
          </div>
        ))}
      </div>
      {open && (
        <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(null) }}
          onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setOpen(null) } }}>
          <div className="modal wide" onMouseDown={(e) => e.stopPropagation()}>
            <header><h2>{open.name}</h2><button autoFocus className="icon-btn" aria-label="Close document" onClick={() => setOpen(null)}><X size={16} /></button></header>
            <pre className="doc-text">{open.text}</pre>
          </div>
        </div>
      )}
    </div>
  )
  usePageContext(() => (embedded ? undefined : {
    view: 'documents',
    label: open ? `Document “${open.name}”` : 'Documents',
    detail: open
      ? `The user has this uploaded document open — id \`${open.id}\`.\n\n${open.text?.slice(0, 4000) ?? ''}`
      : `Uploaded documents, all searchable from any chat:\n${lines(documents, (d) => `${d.name} (\`${d.id}\`, ${d.chunk_count ?? 0} chunks)`)}`,
    refs: open ? [{ kind: 'document', id: open.id, name: open.name }] : documents.slice(0, 40).map((d) => ({ kind: 'document', id: d.id, name: d.name })),
    hints: open ? ['Summarise this document', 'What does it say about…'] : ['What is in my library?']
  }), [documents, open, embedded])

  if (embedded) return body

  return (
    <main className="page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" aria-label="Show sidebar" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><FileText size={16} /> Documents</h2>
        <div className="no-drag header-right">
          <SendToSpace items={[{ kind: 'documents' }]} />
          <ScopeSelect value={libraryScope} onChange={(s) => void setLibraryScope(s)} />
          {uploadBtn}
        </div>
        <AppSwitcher />
      </header>
      {body}
    </main>
  )
}
