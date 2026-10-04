import { useEffect, useRef, useState } from 'react'
import { Upload, Trash2, FileText, Pin, X } from 'lucide-react'
import { useStore, type Scope } from '../store'
import { api } from '../lib/api'
import type { Document } from '@shared/types'
import ProjectChip from './ProjectChip'

const fmtSize = (n: number): string => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`)

/** Uploads, for Files -> Uploads, Settings and a project. `embedded` is accepted for those callers; it is the only mode. */
export default function DocumentsView({ projectId }: { projectId?: string; embedded?: boolean }): JSX.Element {
  const documents = useStore((s) => s.documents)
  const libraryScope = useStore((s) => s.libraryScope)
  const { uploadDocuments, deleteDocument, pinDocument, loadScope } = useStore()
  // Without a project it follows the library scope, which Files -> Uploads picks in its header.
  const scope: Scope = projectId ?? libraryScope
  const fileRef = useRef<HTMLInputElement>(null)
  const [drag, setDrag] = useState(false)
  const [open, setOpen] = useState<Document | null>(null)

  useEffect(() => { void loadScope(scope) }, [scope, loadScope])
  const targetProject = scope === 'all' || scope === 'personal' ? null : scope
  const view = async (d: Document): Promise<void> => setOpen(await api.documents.get(d.id))

  const fileInput = (
    <input id={projectId ? 'doc-upload-input-project' : 'doc-upload-input'} ref={fileRef} type="file" multiple hidden
      onChange={(e) => { if (e.target.files?.length) void uploadDocuments(e.target.files, targetProject); e.target.value = '' }} />
  )
  const uploadBtn = <button className="primary-btn" onClick={() => fileRef.current?.click()}><Upload size={14} /> Upload{targetProject ? ' to project' : ''}</button>

  const body = (
    <div className={`page-body ${drag ? 'dragging' : ''}`} onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
      title="Drop files to upload"
      onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) void uploadDocuments(e.dataTransfer.files, targetProject) }}>
      {fileInput}
      {documents.length > 0 && <div className="add-row">{uploadBtn}<span className="muted small">Drop more files here.</span></div>}
      {documents.length === 0 && (
        <div className="empty-state">
          <FileText size={28} />
          <h2>No uploads yet</h2>
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
              <button className={`icon-btn ghost ${d.pinned ? 'active' : ''}`} aria-pressed={Boolean(d.pinned)} aria-label={`${d.pinned ? 'Unpin' : 'Pin'} ${d.name}`}
                title={d.pinned ? 'Pinned: included in every chat here' : 'Pin into every chat here'}
                onClick={(e) => { e.stopPropagation(); void pinDocument(d.id, !d.pinned) }}><Pin size={13} /></button>
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
  return body
}
