import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, ExternalLink, FileDown, FolderOpen, MessageSquare, Trash2 } from 'lucide-react'
import { useStore } from '../store'
import { saveDownload } from '../lib/api'
import { itemRawPath } from '../lib/showPanel'
import { artifactShowItem, groupByChat, type ChatFile } from '../lib/chatFiles'
import { chatFilesApi } from '../lib/useChatFiles'
import { ChatFileRow } from './ChatFilesPanel'
import UploadPreview from './UploadPreview'
import './chatFiles.css'

/**
 * Files → Artifacts: what the assistant made in any chat (documents, data, images, code, downloads), from plain
 * chats' outboxes and desks' workspaces alike. Grouped by chat, the chat with the newest file first; the group's
 * title opens the chat. A chat in the trash keeps its files here, labelled, with no link, until it is erased.
 * Rows open the file in the viewer; the folder button shows it in Finder. Nothing is copied: each row is a path.
 */
export default function ArtifactsView(): JSX.Element {
  const conversations = useStore((s) => s.conversations)
  const selectChat = useStore((s) => s.selectChat)
  const toast = useStore((s) => s.toast)
  const [files, setFiles] = useState<ChatFile[] | null>(null)
  const [cursor, setCursor] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [open, setOpen] = useState<ChatFile | null>(null)

  const gen = useRef(0)  // bumped on every reload, so a Load more page from before it is dropped
  const reload = useCallback((): void => {
    const g = ++gen.current
    chatFilesApi.artifacts().then((r) => { if (g === gen.current) { setFiles(r.files); setCursor(r.next_cursor); setProblem(null) } })
      .catch((e: Error) => { if (g === gen.current) setProblem(e.message) })
  }, [])
  // The conversation list is replaced whenever a run finishes or a chat comes or goes: the refresh signal.
  useEffect(() => {
    const t = setTimeout(reload, 300)
    return () => clearTimeout(t)
  }, [reload, conversations])

  const more = (): void => {
    if (!cursor || busy) return
    setBusy(true)
    const g = gen.current
    chatFilesApi.artifacts(cursor)
      .then((r) => { if (g !== gen.current) return; setFiles((cur) => [...(cur ?? []), ...r.files]); setCursor(r.next_cursor) })
      .catch((e: Error) => setProblem(e.message))
      .finally(() => setBusy(false))
  }
  const run = (p: Promise<unknown>): void => { p.catch((e: Error) => toast(e.message, 'error')) }

  return (
    <div className="page-body pf artifacts" data-testid="files-artifacts">
      {problem && <p className="empty-hint" role="alert">{problem}</p>}
      {!problem && files === null && <p className="empty-hint">Loading…</p>}
      {files?.length === 0 && (
        <div className="empty-state">
          <FileDown size={28} />
          <h2>No artifacts yet</h2>
          <p>Files the assistant makes in any chat (documents, data, images, code, downloads) show up here.</p>
        </div>
      )}
      {!!files?.length && groupByChat(files).map((g) => (
        <section key={g.conversationId} data-testid="artifact-group">
          <h4>
            {g.deleted
              ? <span className="af-chat deleted" title="This chat is in the trash"><Trash2 size={11} /> {g.title} · deleted chat</span>
              : <button type="button" className="af-chat" title={`Open the chat “${g.title}”`} onClick={() => void selectChat(g.conversationId)}><MessageSquare size={11} /> {g.title}</button>}
          </h4>
          {g.files.map((f) => <ChatFileRow key={f.id} file={f} jump={null} onOpen={setOpen} />)}
        </section>
      ))}
      {cursor && <button type="button" className="ghost-btn cf-more" disabled={busy} onClick={more}>{busy ? 'Loading…' : 'Load more'}</button>}
      {open && (
        <UploadPreview item={artifactShowItem(open)} onClose={() => setOpen(null)} actions={
          <>
            <button className="icon-btn" title="Open in default app" aria-label="Open in default app" onClick={() => run(chatFilesApi.openArtifact(open.id))}><ExternalLink size={14} /></button>
            <button className="icon-btn" title="Reveal in Finder" aria-label="Reveal in Finder" onClick={() => run(chatFilesApi.revealArtifact(open.id))}><FolderOpen size={14} /></button>
            <button className="icon-btn" title="Save a copy" aria-label="Save a copy" onClick={() => run(saveDownload(itemRawPath(artifactShowItem(open)), open.name))}><Download size={14} /></button>
          </>
        } />
      )}
    </div>
  )
}
