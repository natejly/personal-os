import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { groupByChat, type ChatFile, type FilesScope } from '../lib/chatFiles'
import { chatFilesApi } from '../lib/useChatFiles'
import { ChatFileRow } from './ChatFilesPanel'
import './chatFiles.css'

const BY_CHAT_KEY = 'grain.sidebar.filesByChat'
const readByChat = (): boolean => {
  try { return localStorage.getItem(BY_CHAT_KEY) === '1' } catch { return false }
}

/**
 * The sidebar's Files list: everything the personal (no-project) chats touched, newest first, flat or
 * grouped by chat. Reloads from the top when the chat list changes (a run finished, a chat came or went).
 * With a project scope it is that project's newest `limit` files, flat; the project page has the rest.
 */
export default function SidebarChatFiles({ jump, scope = 'personal', limit }: { jump: (conversationId: string) => void; scope?: FilesScope; limit?: number }): JSX.Element {
  const conversations = useStore((s) => s.conversations)
  const projectId = scope === 'personal' ? null : scope.projectId
  // Notes and uploads count in a project, so a change to either reloads it; personal files come only from chats.
  const docs = useStore((s) => (projectId ? s.docs : null))
  const documents = useStore((s) => (projectId ? s.documents : null))
  const [files, setFiles] = useState<ChatFile[] | null>(null)
  const [cursor, setCursor] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [byChat, setByChat] = useState(readByChat)

  const gen = useRef(0)  // bumped on every reload, so a Load more page from before it is dropped
  useEffect(() => {
    let live = true
    const t = setTimeout(() => {
      gen.current += 1
      chatFilesApi.list(scope, null, limit).then((r) => { if (live) { setFiles(r.files); setCursor(r.next_cursor); setProblem(null) } })
        .catch((e: Error) => { if (live) setProblem(e.message) })
    }, 300)
    return () => { live = false; clearTimeout(t) }
  }, [conversations, projectId, docs, documents, limit])  // eslint-disable-line react-hooks/exhaustive-deps -- scope is read through projectId

  const more = (): void => {
    if (!cursor || busy) return
    setBusy(true)
    const g = gen.current
    chatFilesApi.list(scope, cursor)
      .then((r) => { if (g !== gen.current) return; setFiles((cur) => [...(cur ?? []), ...r.files]); setCursor(r.next_cursor) })
      .catch((e: Error) => setProblem(e.message))
      .finally(() => setBusy(false))
  }
  const row = (f: ChatFile, showChat: boolean): JSX.Element => <ChatFileRow key={f.id} file={f} jump={jump} showChat={showChat} />

  return (
    <div className="cf-side">
      {problem && <p className="empty-hint" role="alert">{problem}</p>}
      {!problem && files === null && <p className="empty-hint">Loading…</p>}
      {files?.length === 0 && <p className="empty-hint">{projectId ? 'No files in this project yet.' : 'No files in personal chats yet.'}</p>}
      {!!files?.length && !projectId && (
        <label className="cf-bychat">
          <input type="checkbox" checked={byChat} onChange={(e) => {
            setByChat(e.target.checked)
            try { localStorage.setItem(BY_CHAT_KEY, e.target.checked ? '1' : '0') } catch { /* private mode */ }
          }} /> By chat
        </label>
      )}
      {files && (byChat && !projectId
        ? groupByChat(files).map((g) => <section key={g.conversationId}><h4>{g.title}</h4>{g.files.map((f) => row(f, false))}</section>)
        : files.map((f) => row(f, true)))}
      {cursor && !projectId && <button type="button" className="ghost-btn cf-more" disabled={busy} onClick={more}>{busy ? 'Loading…' : 'Load more'}</button>}
    </div>
  )
}
