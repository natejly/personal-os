import { useEffect, useState } from 'react'
import { File, FileDown, FileText, FolderOpen, Files, GitBranch, Paperclip, Pin } from 'lucide-react'
import { Popover } from '../canvas/PresetsMenu'
import { fmtAgo } from '../lib/deskFiles'
import { actionLabel, groupByKind, rowAction, type ChatFile, type ChatFileKind } from '../lib/chatFiles'
import { chatFilesApi, openChatFile, revealChatFile } from '../lib/useChatFiles'
import './chatFiles.css'

const ICON: Record<ChatFileKind, JSX.Element> = {
  upload: <Paperclip size={13} />,
  note: <FileText size={13} />,
  output: <FileDown size={13} />,
  local: <File size={13} />,
  coding: <GitBranch size={13} />
}

/**
 * One file of a chat: the name opens it (or goes to the chat), the muted line says what happened and when,
 * and a folder button shows paths in Finder. `jump` is how a click on a side-panel file gets to its chat
 * (null when the chat is already on screen); `showChat` adds the chat's title (or "N chats") to the muted line.
 * `onOpen` replaces the default open; `onPin` adds a pin toggle to uploads.
 */
export function ChatFileRow({ file, jump, showChat, onDone, onOpen, onPin }: { file: ChatFile; jump: ((conversationId: string) => void) | null; showChat?: boolean; onDone?: () => void; onOpen?: (f: ChatFile) => void; onPin?: (f: ChatFile) => void }): JSX.Element {
  const goes = rowAction(file) === 'jump-to-chat'
  // In the chat itself a coding session or a missing file has nowhere to jump to: the folder is the useful thing.
  const here = goes && !jump
  const click = (): void => {
    if (file.missing || here) return
    void (onOpen ? onOpen(file) : openChatFile(file, jump))
    onDone?.()
  }
  const canReveal = file.kind !== 'upload' && file.kind !== 'note' && !file.missing
  return (
    <div className={`cf-row${file.missing ? ' missing' : ''}`}>
      <span className="cf-icon">{ICON[file.kind]}</span>
      <button type="button" className="cf-main" disabled={file.missing || (here && !canReveal)} onClick={here ? () => void revealChatFile(file) : click}
        title={goes && !here ? `Open the chat “${file.conversation_title || 'Untitled chat'}”` : file.name}>
        <span className="cf-name">{file.name}</span>
        <span className="cf-meta">
          {file.missing ? 'missing' : actionLabel(file.action)} · {fmtAgo(file.created_at)}
          {showChat && (file.chat_count > 1 ? <> · {file.chat_count} chats</> : file.conversation_id && <> · {file.conversation_title || 'Untitled chat'}</>)}
        </span>
      </button>
      {onPin && file.kind === 'upload' && (
        <button type="button" className={`icon-btn ghost cf-pin${file.pinned ? ' on' : ''}`} aria-pressed={file.pinned} aria-label={`${file.pinned ? 'Unpin' : 'Pin'} ${file.name}`}
          title={file.pinned ? 'Pinned: included in every chat here' : 'Pin into every chat here'} onClick={() => onPin(file)}><Pin size={13} /></button>
      )}
      {canReveal && <button type="button" className="icon-btn ghost cf-reveal" aria-label={`Show ${file.name} in Finder`} title="Show in Finder" onClick={() => void revealChatFile(file)}><FolderOpen size={13} /></button>}
    </div>
  )
}

/** The chat header's Documents button and its popover: this chat's files, grouped by kind. */
export default function ChatFilesButton({ conversationId }: { conversationId?: string }): JSX.Element {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null)
  const [files, setFiles] = useState<ChatFile[] | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  useEffect(() => {
    if (!at || !conversationId) return
    let live = true  // ChatView is not keyed by chat, so a late answer for the last chat must not land here
    setFiles(null)
    setProblem(null)
    chatFilesApi.forChat(conversationId).then((r) => { if (live) setFiles(r.files) }).catch((e: Error) => { if (live) setProblem(e.message) })
    return () => { live = false }
  }, [at, conversationId])
  const close = (): void => setAt(null)
  return (
    <>
      <button className={`icon-btn no-drag${at ? ' on' : ''}`} title="Documents in this chat" aria-label="Documents in this chat" aria-haspopup="dialog" aria-expanded={!!at} disabled={!conversationId}
        onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); setAt({ x: r.right - 320, y: r.bottom + 4 }) }}><Files size={15} /></button>
      {at && (
        <Popover at={at} onClose={close} className="cf-pop">
          {problem && <p className="empty-hint" role="alert">{problem}</p>}
          {!problem && files === null && <p className="empty-hint">Loading…</p>}
          {files?.length === 0 && <p className="empty-hint">No documents in this chat yet.</p>}
          {files && groupByKind(files).map((g) => (
            <section key={g.kind}>
              <h4>{g.label}</h4>
              {g.files.map((f) => <ChatFileRow key={f.id} file={f} jump={null} onDone={close} />)}
            </section>
          ))}
        </Popover>
      )}
    </>
  )
}
