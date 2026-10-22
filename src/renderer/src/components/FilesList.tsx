import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Download, ExternalLink, FolderOpen, FileText, Package, Pin, Trash2 } from '../icons'
import { useStore } from '../store'
import { saveDownload } from '../lib/api'
import { itemRawPath } from '../lib/showPanel'
import { artifactShowItem, keepsChat, keepsUploads, projectFilter, type ChatFile } from '../lib/chatFiles'
import { chatFilesApi } from '../lib/useChatFiles'
import { rowButton } from '../lib/rowButton'
import { FilesEmpty } from './FilesUpload'
import UploadPreview from './UploadPreview'
import './chatFiles.css'

const day = (t: number): string => new Date(t * 1000).toLocaleDateString()

/**
 * The non-note rows of the Files list: uploads (the stored documents for the project filter) and chat files (every
 * chat's outputs, newest first, paged). One row per file with its kind icon and date; a chat file also names its chat,
 * which jumps there. A click opens the file in the viewer. `query` matches names, and chat titles for chat files.
 */
export default function FilesList({ kind, query, project }: { kind: string; query: string; project: string | null }): JSX.Element | null {
  const documents = useStore((s) => s.documents)
  const conversations = useStore((s) => s.conversations)
  const loadScope = useStore((s) => s.loadScope)
  const openUploadPreview = useStore((s) => s.openUploadPreview)
  const deleteDocument = useStore((s) => s.deleteDocument)
  const pinDocument = useStore((s) => s.pinDocument)
  const selectChat = useStore((s) => s.selectChat)
  const toast = useStore((s) => s.toast)
  const withUploads = keepsUploads(kind)
  const withChat = keepsChat(kind)
  const [files, setFiles] = useState<ChatFile[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState<ChatFile | null>(null)
  const gen = useRef(0)  // bumped on every reload, so a Load more page from before it is dropped

  useEffect(() => { if (withUploads) void loadScope(project ?? 'all') }, [withUploads, project, loadScope])
  const reload = useCallback((): void => {
    const g = ++gen.current
    chatFilesApi.artifacts().then((r) => { if (g === gen.current) { setFiles(r.files); setCursor(r.next_cursor) } }).catch(() => {})
  }, [])
  // The conversation list is replaced whenever a run finishes or a chat comes or goes: the refresh signal.
  useEffect(() => {
    if (!withChat) return
    const t = setTimeout(reload, 300)
    return () => clearTimeout(t)
  }, [reload, conversations, withChat])
  const more = (): void => {
    if (!cursor || busy) return
    setBusy(true)
    const g = gen.current
    chatFilesApi.artifacts(cursor)
      .then((r) => { if (g !== gen.current) return; setFiles((cur) => [...cur, ...r.files]); setCursor(r.next_cursor) })
      .catch((e: Error) => toast(e.message, 'error'))
      .finally(() => setBusy(false))
  }
  const run = (p: Promise<unknown>): void => { p.catch((e: Error) => toast(e.message, 'error')) }

  const q = query.trim().toLowerCase()
  const uploads = useMemo(() => (withUploads ? documents.filter((d) => (!project || d.project_id === project) && (!q || d.name.toLowerCase().includes(q))) : []), [withUploads, documents, project, q])
  const chat = useMemo(() => (withChat ? projectFilter(files.filter((f) => !project || f.project_id === project), query) : []), [withChat, files, project, query])

  if (uploads.length === 0 && chat.length === 0) return kind === 'all' || q || !(withUploads || withChat) ? null : <FilesEmpty />
  return (
    <>
      {uploads.map((d) => (
        <div key={d.id} className="doc-row" data-testid="upload-row" {...rowButton(() => openUploadPreview(d.id))}>
          <FileText size={13} className="doc-row-icon" />
          <span className="doc-row-main"><span className="doc-row-title"><span className="doc-row-name" title={d.name}>{d.name}</span></span></span>
          <button className={`icon-btn ghost xs ${d.pinned ? 'starred' : ''}`} aria-pressed={Boolean(d.pinned)} aria-label={`${d.pinned ? 'Unpin' : 'Pin'} ${d.name}`} title={d.pinned ? 'Unpin' : 'Pin'}
            onClick={(e) => { e.stopPropagation(); void pinDocument(d.id, !d.pinned) }}><Pin size={12} fill={d.pinned ? 'currentColor' : 'none'} /></button>
          <button className="icon-btn ghost xs danger" aria-label={`Delete ${d.name}`} title="Delete" onClick={(e) => { e.stopPropagation(); void deleteDocument(d.id) }}><Trash2 size={12} /></button>
          <span className="doc-row-date">{day(d.created_at)}</span>
        </div>
      ))}
      {chat.map((f) => (
        <div key={f.id} className="doc-row has-meta" data-testid="artifact-row" {...rowButton(() => setOpen(f))}>
          <Package size={13} className="doc-row-icon" />
          <span className="doc-row-main">
            <span className="doc-row-title"><span className="doc-row-name" title={f.name}>{f.name}</span></span>
            <span className="doc-row-meta">
              {f.conversation_id && !f.chat_deleted
                ? <button type="button" className="link doc-row-chat" onClick={(e) => { e.stopPropagation(); void selectChat(f.conversation_id as string) }}>{f.conversation_title || 'Untitled chat'}</button>
                : <span>{f.conversation_title || 'Untitled chat'}</span>}
            </span>
          </span>
          <span className="doc-row-date">{day(f.created_at)}</span>
        </div>
      ))}
      {withChat && cursor && <button type="button" className="ghost-btn cf-more" disabled={busy} onClick={more}>{busy ? 'Loading…' : 'Load more'}</button>}
      {open && (
        <UploadPreview item={artifactShowItem(open)} onClose={() => setOpen(null)} actions={
          <>
            <button className="icon-btn" title="Open in default app" aria-label="Open in default app" onClick={() => run(chatFilesApi.openArtifact(open.id))}><ExternalLink size={14} /></button>
            <button className="icon-btn" title="Reveal in Finder" aria-label="Reveal in Finder" onClick={() => run(chatFilesApi.revealArtifact(open.id))}><FolderOpen size={14} /></button>
            <button className="icon-btn" title="Save a copy" aria-label="Save a copy" onClick={() => run(saveDownload(itemRawPath(artifactShowItem(open)), open.name))}><Download size={14} /></button>
          </>
        } />
      )}
    </>
  )
}
