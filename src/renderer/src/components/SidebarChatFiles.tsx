import { useEffect, useState } from 'react'
import { useStore } from '../store'
import { groupByChat, type ChatFile } from '../lib/chatFiles'
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
 */
export default function SidebarChatFiles({ jump }: { jump: (conversationId: string) => void }): JSX.Element {
  const conversations = useStore((s) => s.conversations)
  const [files, setFiles] = useState<ChatFile[] | null>(null)
  const [cursor, setCursor] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [byChat, setByChat] = useState(readByChat)

  useEffect(() => {
    let live = true
    const t = setTimeout(() => {
      chatFilesApi.list('personal').then((r) => { if (live) { setFiles(r.files); setCursor(r.next_cursor); setProblem(null) } })
        .catch((e: Error) => { if (live) setProblem(e.message) })
    }, 300)
    return () => { live = false; clearTimeout(t) }
  }, [conversations])

  const more = (): void => {
    if (!cursor || busy) return
    setBusy(true)
    chatFilesApi.list('personal', cursor)
      .then((r) => { setFiles((cur) => [...(cur ?? []), ...r.files]); setCursor(r.next_cursor) })
      .catch((e: Error) => setProblem(e.message))
      .finally(() => setBusy(false))
  }
  const row = (f: ChatFile, showChat: boolean): JSX.Element => <ChatFileRow key={f.id} file={f} jump={jump} showChat={showChat} />

  return (
    <div className="cf-side">
      {problem && <p className="empty-hint" role="alert">{problem}</p>}
      {!problem && files === null && <p className="empty-hint">Loading…</p>}
      {files?.length === 0 && <p className="empty-hint">No files in personal chats yet.</p>}
      {!!files?.length && (
        <label className="cf-bychat">
          <input type="checkbox" checked={byChat} onChange={(e) => {
            setByChat(e.target.checked)
            try { localStorage.setItem(BY_CHAT_KEY, e.target.checked ? '1' : '0') } catch { /* private mode */ }
          }} /> By chat
        </label>
      )}
      {files && (byChat
        ? groupByChat(files).map((g) => <section key={g.conversationId}><h4>{g.title}</h4>{g.files.map((f) => row(f, false))}</section>)
        : files.map((f) => row(f, true)))}
      {cursor && <button type="button" className="ghost-btn cf-more" disabled={busy} onClick={more}>{busy ? 'Loading…' : 'Load more'}</button>}
    </div>
  )
}
