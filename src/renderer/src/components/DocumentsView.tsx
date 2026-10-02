import { useEffect, useRef, useState } from 'react'
import { Upload, Trash2, FileText, X } from 'lucide-react'
import { useStore, type Scope } from '../store'
import { api } from '../lib/api'
import type { Document } from '@shared/types'
import ProjectChip from './ProjectChip'

const fmtSize = (n: number): string => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`)

/**
 * The uploaded-document library. Always hosted by another view (a project, or Settings → Knowledge
 * base), so it is a body with no page header of its own. `embedded` is still accepted because
 * ProjectView passes it; it no longer changes anything.
 */
export default function DocumentsView({ projectId }: { projectId?: string; embedded?: boolean }): JSX.Element {
  const documents = useStore((s) => s.documents)
  const libraryScope = useStore((s) => s.libraryScope)
  const { uploadDocuments, deleteDocument, loadScope } = useStore()
  // Without a project (Settings → Knowledge base) it follows the library scope.
  const scope: Scope = projectId ?? libraryScope
  const fileRef = useRef<HTMLInputElement>(null)
  const [drag, setDrag] = useState(false)
  const [open, setOpen] = useState<Document | null>(null)

  useEffect(() => { void loadScope(scope) }, [scope, loadScope])
  const targetProject = scope === 'all' || scope === 'personal' ? null : scope
  const view = async (d: Document): Promise<void> => setOpen(await api.documents.get(d.id))

  return (
    <div className={`page-body ${drag ? 'dragging' : ''}`} onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
      title="Drop files to upload"
      onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) void uploadDocuments(e.dataTransfer.files, targetProject) }}>
      <input id={projectId ? 'doc-upload-input-project' : 'doc-upload-input'} ref={fileRef} type="file" multiple hidden
        onChange={(e) => { if (e.target.files?.length) void uploadDocuments(e.target.files, targetProject); e.target.value = '' }} />
      {documents.length > 0 && (
        <div className="add-row">
          <button className="primary-btn" onClick={() => fileRef.current?.click()}><Upload size={14} /> Upload{targetProject ? ' to project' : ''}</button>
          <span className="muted small">Drop more files here.</span>
        </div>
      )}
      {documents.length === 0 && (
        <div className="empty-state">
          <FileText size={28} />
          <h2>No documents yet</h2>
          <p>Drop files here, or upload them.</p>
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
}
