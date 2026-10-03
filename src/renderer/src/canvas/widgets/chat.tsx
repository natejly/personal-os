import { useEffect, useRef, useState, type DragEvent } from 'react'
import { Check, MessageSquare, MessagesSquare, Pencil } from 'lucide-react'
import type { CanvasWindow, DragKind, DragPayload } from '@shared/types'
import MessageView from '../../components/Message'
import RegenRow from '../../components/RegenRow'
import Composer from '../../components/Composer'
import ChatControls from '../../components/ChatControls'
import { api } from '../../lib/api'
import { uploadNote } from '../../lib/uploadNote'
import { retainSession, useConversation, useIsStreaming, useStore, useStreamingMessageId } from '../../store'
import { useDropTarget } from '../dnd'
import type { WidgetDef, WidgetProps } from '../registry'
import { useCanvas } from '../store'
import { useRingStatus } from '../useRingStatus'

const ACCEPTS: DragKind[] = ['todo', 'document', 'memory', 'file']

/**
 * `Composer` keeps its draft in local state and belongs to another slice, so a drop reaches it the way
 * a keystroke would: the prototype value setter (React's change tracker only notices a value it did
 * not write itself) plus the `input` event its `onChange` is delegated from. The handoff note asks for
 * a real per-conversation draft on the store, which would retire this.
 */
const appendDraft = (root: HTMLElement | null, text: string): boolean => {
  const el = root?.querySelector('textarea')
  const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
  if (!el || !set) return false
  set.call(el, el.value.trim() ? `${el.value.replace(/\s+$/, '')}\n\n${text}` : text)
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.focus()
  return true
}

/** Create a fresh conversation in the window's project and point the window at it. */
const newChatFor = async (win: CanvasWindow): Promise<string | null> => {
  const app = useStore.getState()
  try {
    const c = await api.conversations.create(win.project_id ?? null, app.settings.defaultModel)
    void app.refreshConversations()
    await useCanvas.getState().setWindowRef(win.id, c.id)
    return c.id
  } catch (e) {
    app.toast((e as Error).message, 'error')
    return null
  }
}

/** Re-point this window at any conversation (every project — a window is cross-scope), or a new one. */
const ChatSwitcher = ({ win, convId }: { win: CanvasWindow; convId: string }): JSX.Element => {
  const conversations = useStore((s) => s.conversations)
  const refreshConversations = useStore((s) => s.refreshConversations)
  const setWindowRef = useCanvas((s) => s.setWindowRef)
  const onPick = async (v: string): Promise<void> => {
    if (v === '__new__') await newChatFor(win)
    else if (v !== convId) await setWindowRef(win.id, v)
  }
  return (
    // The list may be stale (chats made in other windows); refresh as the menu opens.
    <label className="icon-btn ghost xs chat-switch" title="Switch chat" onMouseDown={() => void refreshConversations()}>
      <MessagesSquare size={11} />
      <select aria-label="Switch chat" value={convId} onChange={(e) => void onPick(e.target.value)}>
        <option value="__new__">+ New chat</option>
        {!conversations.some((c) => c.id === convId) && <option value={convId} disabled>(current chat)</option>}
        {conversations.map((c) => <option key={c.id} value={c.id}>{c.title || 'Untitled chat'}</option>)}
      </select>
    </label>
  )
}

/** The window has no title bar, so the chat names itself. Double-click or the pencil renames it. */
const ChatTitle = ({ convId, title, switcher }: { convId: string; title: string; switcher?: JSX.Element }): JSX.Element => {
  const renameChat = useStore((s) => s.renameChat)
  const [editing, setEditing] = useState<string | null>(null)
  const commit = (): void => {
    const next = (editing ?? '').trim()
    setEditing(null)
    if (next && next !== title) void renameChat(convId, next)
  }
  if (editing !== null) {
    return (
      <div className="chat-head">
        <input autoFocus className="chat-title-input" value={editing}
          onChange={(e) => setEditing(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') setEditing(null) }} />
        <button className="icon-btn ghost xs" title="Save" onMouseDown={(e) => e.preventDefault()} onClick={commit}><Check size={12} /></button>
      </div>
    )
  }
  return (
    <div className="chat-head">
      <span className="chat-title" title={title} onDoubleClick={() => setEditing(title)}>{title || 'Untitled chat'}</span>
      <button className="icon-btn ghost xs chat-rename" title="Rename chat" onClick={() => setEditing(title)}><Pencil size={11} /></button>
      {switcher}
    </div>
  )
}

function ChatWidget({ window: win, live, onTitle }: WidgetProps): JSX.Element {
  const convId = win.ref_id ?? ''
  const root = useRef<HTMLDivElement>(null)
  const scroll = useRef<HTMLDivElement>(null)
  const titled = useRef(win.title)
  const [stick, setStick] = useState(true)
  const [gone, setGone] = useState(false)
  const [draft, setDraft] = useState('')
  const conversations = useStore((s) => s.conversations)
  const convo = useConversation(convId)
  const loaded = useStore((s) => !!s.sessions[convId])
  const streaming = useIsStreaming(convId)
  const streamingId = useStreamingMessageId(convId)
  const { status } = useRingStatus(convId)

  // An on-screen window is not an LRU victim for as long as it is mounted.
  useEffect(() => (convId ? retainSession(convId) : undefined), [convId])

  // `attachSession`, not `openSession`: a window opened over a reply already in flight adopts that run
  // from its own seq, so it paints amber at once instead of waiting for the next one. Runs even when
  // the session is already loaded — attaching is idempotent (shared fetch, run dedupe) and a loaded
  // session can still be missing a run someone else started. Keyed on `loaded` as well as the ref, so
  // a session that disappears anyway — closed from another surface, its project deleted — is
  // refetched instead of leaving the window on the empty state forever.
  useEffect(() => {
    if (!convId) return
    setGone(false)
    let alive = true
    void useStore.getState().attachSession(convId).catch(() => { if (alive) setGone(true) })
    return () => { alive = false }
  }, [convId, loaded])

  // The window keeps the conversation's name for as long as nobody renamed the window by hand.
  useEffect(() => {
    const t = convo?.title
    if (!t || t === win.title || (win.title !== '' && win.title !== titled.current)) return
    titled.current = t
    onTitle(t)
  }, [convo?.title, win.title, onTitle])

  const msgs = convo?.messages ?? []
  const lastLen = msgs[msgs.length - 1]?.content.length ?? 0
  useEffect(() => {
    if (stick && live) scroll.current?.scrollTo({ top: scroll.current.scrollHeight })
  }, [lastLen, msgs.length, stick, live])

  const onDrop = async (p: DragPayload | null, e: DragEvent<HTMLElement>): Promise<void> => {
    const app = useStore.getState()
    const files = e.dataTransfer.files
    const draft = (text: string): void => {
      if (!appendDraft(root.current, text)) app.toast('Could not reach the composer', 'error')
    }
    if (!p || p.kind === 'file') {
      if (!files.length) return
      const saved = await app.uploadDocuments(files, convo?.project_id ?? null)
      if (!saved.length) return
      await app.noteUntrustedUpload(convId || undefined, 'draft')
      const { note } = uploadNote(saved)
      if (note) draft(note)
      return
    }
    switch (p.kind) {
      case 'todo':
        draft(`> ${p.label}`)
        break
      // Contract §12.4: there is no per-conversation attachment, so the nearest real action is an
      // instruction the model can execute itself.
      case 'document':
        draft(`Read document ${p.id} ("${p.label}") with read_document and use it as context.`)
        break
      case 'memory':
        await app.updateMemory(p.id, { pinned: true })
        app.toast(`Pinned "${p.label}" into context`)
        break
    }
  }

  const drop = useDropTarget(ACCEPTS, (p, e) => void onDrop(p, e))

  if (!convId) return <div className="widget-empty">A chat window needs a conversation.</div>
  if (gone) {
    // The conversation was deleted from another surface; the window lives on, so give it a future:
    // type to start a fresh chat in place, or re-point the window at an existing one.
    const sendNew = async (): Promise<void> => {
      const id = await newChatFor(win)
      if (!id) return
      const text = draft.trim()
      setDraft('')
      if (text) void useStore.getState().send(text, id)
    }
    const others = conversations.filter((c) => c.id !== convId)
    return (
      <div className="widget chat-gone">
        <MessageSquare size={18} />
        <p className="widget-sub">This chat was deleted.</p>
        <div className="chat-gone-send">
          <input autoFocus aria-label="Message for a new chat" placeholder="Send a message to start a new chat…"
            value={draft} onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void sendNew() }} />
          <button className="primary-btn" onClick={() => void sendNew()}>{draft.trim() ? 'Send' : 'New chat'}</button>
        </div>
        {others.length > 0 && (
          <>
            <p className="widget-sub">or pick up an existing chat:</p>
            <div className="chat-gone-list">
              {others.slice(0, 6).map((c) => (
                <button key={c.id} className="ghost-btn" title={c.title || 'Untitled chat'}
                  onClick={() => void useCanvas.getState().setWindowRef(win.id, c.id)}>
                  <MessageSquare size={12} /> {c.title || 'Untitled chat'}
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    )
  }

  // Off-screen, minimized or zoomed out: the message list unmounts, so streamed tokens stop
  // re-rendering it, while the ring stays mounted and the session keeps running.
  if (!live) {
    return (
      <div className="proxy-card">
        <MessageSquare size={18} />
        <strong>{convo?.title ?? 'Chat'}</strong>
        <span>{status === 'working' ? 'Still generating…' : 'Paused while off-screen'}</span>
      </div>
    )
  }

  const last = msgs[msgs.length - 1]
  const onScroll = (): void => {
    const el = scroll.current
    if (el) setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 80)
  }
  return (
    <div ref={root} className={drop.over ? 'widget drop-over' : 'widget'} {...drop.handlers}>
      <ChatTitle convId={convId} title={convo?.title ?? ''} switcher={<ChatSwitcher win={win} convId={convId} />} />
      <div className="messages" ref={scroll} onScroll={onScroll}>
        <div className="messages-inner">
          {msgs.map((m) => <MessageView key={m.id} message={m} streaming={streaming && streamingId === m.id} last={m.id === last?.id} />)}
          <RegenRow conversationId={convId} last={last} streaming={streaming} />
          {!msgs.length && <p className="widget-sub">No messages yet.</p>}
        </div>
      </div>
      <Composer conversationId={convId} compact footer={<ChatControls conversationId={convId} />} />
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'chat',
  label: 'Chat',
  icon: <MessageSquare size={18} />,
  defaultSize: { w: 520, h: 640 },
  minSize: { w: 360, h: 320 },
  chrome: 'full',
  statusful: true,
  needsRef: true,
  accepts: ACCEPTS,
  Component: ChatWidget
}

export default ChatWidget
