import { useCallback, useEffect, useRef, useState } from 'react'
import { FileText, Upload } from 'lucide-react'
import { useStore } from '../store'
import { groupByChat, groupByKind, projectFilter, type ChatFile } from '../lib/chatFiles'
import { chatFilesApi } from '../lib/useChatFiles'
import { ChatFileRow } from './ChatFilesPanel'
import './chatFiles.css'

/**
 * A project's Files column: its chats' files, notes and uploads in one list, by type or by chat, with search
 * and upload. Reloads from the top when a chat, note or upload changes. Files open the way they do anywhere
 * else: in their chat's side panel, in the editor, in Finder, or (an upload no chat used) in the upload viewer.
 */
export default function ProjectContext({ projectId }: { projectId: string }): JSX.Element {
  const conversations = useStore((s) => s.conversations)
  const docs = useStore((s) => s.docs)
  const documents = useStore((s) => s.documents)
  const { uploadDocuments, pinDocument, selectChat } = useStore()
  const [files, setFiles] = useState<ChatFile[] | null>(null)
  const [cursor, setCursor] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [byChat, setByChat] = useState(false)
  const [drag, setDrag] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const gen = useRef(0)  // bumped on every reload, so a Load more page from before it is dropped
  const reload = useCallback((): void => {
    const g = ++gen.current  // a slower earlier reload must not overwrite a newer one
    chatFilesApi.list({ projectId }).then((r) => { if (g === gen.current) { setFiles(r.files); setCursor(r.next_cursor); setProblem(null) } })
      .catch((e: Error) => { if (g === gen.current) setProblem(e.message) })
  }, [projectId])
  useEffect(() => {
    const t = setTimeout(reload, 300)
    return () => clearTimeout(t)
  }, [reload, conversations, docs, documents])

  const more = (): void => {
    if (!cursor || busy) return
    setBusy(true)
    const g = gen.current
    chatFilesApi.list({ projectId }, cursor)
      .then((r) => { if (g !== gen.current) return; setFiles((cur) => [...(cur ?? []), ...r.files]); setCursor(r.next_cursor) })
      .catch((e: Error) => setProblem(e.message))
      .finally(() => setBusy(false))
  }
  const upload = (list: FileList | null): void => { if (list?.length) void uploadDocuments(list, projectId) }
  const row = (f: ChatFile, showChat: boolean): JSX.Element => (
    <ChatFileRow key={f.id} file={f} jump={(id) => void selectChat(id)} showChat={showChat}
      onPin={(x) => void pinDocument(x.ref, !x.pinned).then(reload)} />
  )
  const shown = projectFilter(files ?? [], q)
  const uploadBtn = <button className="primary-btn" onClick={() => fileRef.current?.click()}><Upload size={14} /> Upload</button>

  return (
    <div className={`pf${drag ? ' dragging' : ''}`} title="Drop files to upload" data-testid="project-context"
      onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files) }}>
      <input id="doc-upload-input-project" ref={fileRef} type="file" multiple hidden onChange={(e) => { upload(e.target.files); e.target.value = '' }} />
      {problem && <p className="empty-hint" role="alert">{problem}</p>}
      {!problem && files === null && <p className="empty-hint">Loading…</p>}
      {files?.length === 0 && (
        <div className="empty-state">
          <FileText size={28} />
          <h2>No files yet</h2>
          <p>Uploads, notes and what this project&apos;s chats make or read show up here. Drop files anywhere on this page.</p>
          {uploadBtn}
        </div>
      )}
      {!!files?.length && (
        <div className="pf-bar">
          {uploadBtn}
          <input type="search" className="pf-search" placeholder="Search files" aria-label="Search files" value={q} onChange={(e) => setQ(e.target.value)} />
          <span className="cf-seg" role="group" aria-label="Group files">
            <button className={byChat ? '' : 'on'} aria-pressed={!byChat} onClick={() => setByChat(false)}>By type</button>
            <button className={byChat ? 'on' : ''} aria-pressed={byChat} onClick={() => setByChat(true)}>By chat</button>
          </span>
        </div>
      )}
      {!!files?.length && !shown.length && <p className="empty-hint">Nothing in this project's files matches.</p>}
      {byChat
        ? groupByChat(shown).map((g) => <section key={g.conversationId}><h4>{g.title}</h4>{g.files.map((f) => row(f, false))}</section>)
        : groupByKind(shown).map((g) => <section key={g.kind}><h4>{g.label}</h4>{g.files.map((f) => row(f, true))}</section>)}
      {cursor && <button type="button" className="ghost-btn cf-more" disabled={busy} onClick={more}>{busy ? 'Loading…' : 'Load more'}</button>}
    </div>
  )
}
