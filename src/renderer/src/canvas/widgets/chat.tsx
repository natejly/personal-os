import { useEffect, useRef, useState, type DragEvent } from 'react'
import { Check, MessageSquare, MessagesSquare, Pencil, Smile } from 'lucide-react'
import type { Attachment, CanvasWindow, DragKind, DragPayload } from '@shared/types'
import MessageView from '../../components/Message'
import RegenRow from '../../components/RegenRow'
import Composer from '../../components/Composer'
import Face from '../../components/Face'
import ChatControls from '../../components/ChatControls'
import { api } from '../../lib/api'
import { uploadNote } from '../../lib/uploadNote'
import { composerKey, setDraftFiles } from '../../lib/drafts'
import { chatBrowserSession, latestBrowserMessage } from '../../lib/browserApproval'
import { retainSession, useChatFace, useConversation, useIsStreaming, useStore, useStreamingMessageId, useSubagents } from '../../store'
import { useDropTarget } from '../dnd'
import type { WidgetDef, WidgetProps } from '../registry'
import { useCanvas, viewport } from '../store'
import { useRingStatus } from '../useRingStatus'

const ACCEPTS: DragKind[] = ['todo', 'document', 'memory', 'file']
const DEFAULT_SIZE = { w: 520, h: 640 }
/** Blob view: the window is just the creature, this big, with no frame around it. */
const BLOB = { w: 120, h: 120 }
type Size = { w: number; h: number }

/** What the blob's pose means, for its tooltip; the ring's words, in the user's. */
const BLOB_LABEL: Record<string, string> = {
  working: 'thinking…', 'needs-approval': 'needs your approval', done: 'finished', error: 'something went wrong'
}

/** Blob view: the window's own choice, else Settings › Behavior › Compact chats. */
const isBlob = (win: CanvasWindow, compactOn: boolean): boolean => typeof win.config.blob === 'boolean' ? win.config.blob : compactOn

/**
 * Resize the frame about its centre the way a drag would -- optimistic rect, debounced layout PUT --
 * kept inside the visible plane, and remember something in config alongside. Both writes land in the
 * same render, so a body that mounts on the way (the composer) measures itself in its final frame.
 */
const resizeTo = (win: CanvasWindow, size: Size, config?: Record<string, unknown>): void => {
  const st = useCanvas.getState()
  const v = viewport()
  const left = -v.panX / v.zoom
  const top = -v.panY / v.zoom
  const x = Math.max(left, Math.min(win.x + (win.w - size.w) / 2, left + v.width / v.zoom - size.w))
  const y = Math.max(top, Math.min(win.y + (win.h - size.h) / 2, top + v.height / v.zoom - size.h))
  st.patchWindow(win.id, { ...size, x: Math.round(x), y: Math.round(y) })
  st.markLayoutDirty([win.id])
  if (config) void st.setWindowConfig(win.id, config)
}

/**
 * The blob's face, with the current run's subagents around it: the orchestrator in the middle, a small face per child
 * on a ring, a line to each. Clicking a child opens its transcript; the rest of the blob still opens the chat.
 */
function CrewRing({ convId, status, title }: { convId: string; status: string; title: string }): JSX.Element {
  const conv = useConversation(convId)
  const latest = [...(conv?.messages ?? [])].reverse().find((m) => m.role === 'assistant')?.id // the store keeps older replies' children too
  const kids = Object.values(useSubagents(convId)).filter((k) => k.message_id === latest)
  const face = useChatFace(conv)
  const openSubagent = useStore((s) => s.openSubagent)
  if (kids.length === 0) return <Face name={face.name} hue={face.hue} status={status} size="fill" title={title} />
  const R = 38 // ring radius, in % of the frame
  const at = (i: number): { x: number; y: number } => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / kids.length
    return { x: 50 + R * Math.cos(a), y: 50 + R * Math.sin(a) }
  }
  return (
    <span className="crew-ring">
      <svg className="crew-lines" viewBox="0 0 100 100" aria-hidden>
        {kids.map((k, i) => { const p = at(i); return <line key={k.id} x1={50} y1={50} x2={p.x} y2={p.y} /> })}
      </svg>
      <span className="crew-center"><Face name={face.name} hue={face.hue} status={status} size="fill" title={title} /></span>
      {kids.map((k, i) => {
        const p = at(i)
        return (
          <button key={k.id} className="crew-sat" style={{ left: `${p.x}%`, top: `${p.y}%` }} title={`${k.role}: ${k.now || k.state}`}
            onPointerDown={(e) => e.stopPropagation()} onClick={(e) => { e.stopPropagation(); openSubagent(k.id) }}>
            <Face name={k.id} status={k.state} size="fill" />
          </button>
        )
      })}
    </span>
  )
}

/** Fold a chat window to its blob, or grow it back to the size it had. */
const setBlob = (win: CanvasWindow, on: boolean): void => {
  if (on) resizeTo(win, BLOB, { blob: true, full_size: { w: win.w, h: win.h } })
  else resizeTo(win, (win.config.full_size as Size | undefined) ?? DEFAULT_SIZE, { blob: false })
}

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
const ChatTitle = ({ convId, title, actions }: { convId: string; title: string; actions?: JSX.Element }): JSX.Element => {
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
      {actions}
    </div>
  )
}

function ChatWidget({ window: win, live, onConfig, onTitle, onMove }: WidgetProps): JSX.Element {
  const convId = win.ref_id ?? ''
  const root = useRef<HTMLDivElement>(null)
  const scroll = useRef<HTMLDivElement>(null)
  const titled = useRef(win.title)
  const [stick, setStick] = useState(true)
  const [gone, setGone] = useState(false)
  const [draft, setDraft] = useState('')
  const conversations = useStore((s) => s.conversations)
  const convo = useConversation(convId)
  const face = useChatFace(convo)
  const loaded = useStore((s) => !!s.sessions[convId])
  const streaming = useIsStreaming(convId)
  const streamingId = useStreamingMessageId(convId)
  const { status } = useRingStatus(convId)
  // Blob view: the window shows only this chat's creature, posed by this chat's own state. A window
  // that has never chosen follows Settings › Behavior › Compact chats; the head's button overrides it.
  const compactOn = useStore((s) => !!s.settings.compactChats)
  const blob = isBlob(win, compactOn)
  // Where the blob was pressed: a press that travels is a drag, one that stays is the click that opens.
  const pressed = useRef<{ x: number; y: number } | null>(null)

  // The frame follows the view. The buttons resize as they switch (setBlob); this catches the rest: the
  // setting flipping with windows already on the space, or a window that arrives at the wrong size.
  useEffect(() => {
    if (!convId) return
    if (blob && win.h > BLOB.h) setBlob(win, true)
    else if (!blob && win.h <= BLOB.h) setBlob(win, false)
  }, [blob, win.h])

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
    // This window's composer keeps its draft under the conversation's key (lib/drafts.ts).
    const attachFiles = (added: Attachment[]): void =>
      setDraftFiles(composerKey({ conversationId: convId }), (cur) => [...cur, ...added.filter((a) => !cur.some((c) => c.id === a.id))])
    if (!p || p.kind === 'file') {
      if (!files.length) return
      const saved = await app.uploadDocuments(files, convo?.project_id ?? null)
      if (!saved.length) return
      await app.noteUntrustedUpload(convId || undefined, 'draft')
      const { files: added } = uploadNote(saved)
      if (added.length) attachFiles(added)
      return
    }
    switch (p.kind) {
      case 'todo':
        draft(`> ${p.label}`)
        break
      // A stored file dropped from the Files view rides along as an attachment of the next send.
      case 'document':
        attachFiles([{ id: p.id, name: p.label, mime: '' }])
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

  // Blob view: the creature alone, in a frame the CSS strips bare, posed by the same status the ring
  // shows (thinking while working, surprised when it needs you, happy for a moment when done, sad on
  // an error). It is cheap, so it stays up and keeps its pose off-screen too.
  if (blob) {
    return (
      <button className="chat-blob" title={`${convo?.title || 'Chat'} · ${BLOB_LABEL[status] ?? 'click to open, drag to move'}`}
        onPointerDown={(e) => { pressed.current = { x: e.clientX, y: e.clientY }; onMove?.(e) }}
        onClick={(e) => {
          const p = pressed.current
          if (!p || Math.hypot(e.clientX - p.x, e.clientY - p.y) < 4) setBlob(win, false)
        }}>
        <CrewRing convId={convId} status={status} title={convo?.title || 'Chat'} />
      </button>
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

  const actions = (
    <>
      <ChatSwitcher win={win} convId={convId} />
      <button className="icon-btn ghost xs" title="Shrink to a face" aria-label="Shrink to a face" onClick={() => setBlob(win, true)}><Smile size={11} /></button>
    </>
  )

  const last = msgs[msgs.length - 1]
  const watchId = latestBrowserMessage(msgs)
  const onScroll = (): void => {
    const el = scroll.current
    if (el) setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 80)
  }
  return (
    <div ref={root} className={drop.over ? 'widget drop-over' : 'widget'} {...drop.handlers}>
      <ChatTitle convId={convId} title={convo?.title ?? ''} actions={actions} />
      <div className="messages" ref={scroll} onScroll={onScroll}>
        <div className="messages-inner">
          {msgs.map((m) => <MessageView key={m.id} message={m} face={face} streaming={streaming && streamingId === m.id} last={m.id === last?.id}
            browserSession={m.id === watchId ? chatBrowserSession(m.conversation_id) : undefined} />)}
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
  defaultSize: DEFAULT_SIZE,
  minSize: { w: 360, h: 320 },
  chrome: 'full',
  statusful: true,
  needsRef: true,
  menu: (win) =>
    isBlob(win, !!useStore.getState().settings.compactChats)
      ? [{ label: 'Open chat', icon: <MessageSquare size={14} />, run: () => setBlob(win, false) }]
      : [{ label: 'Shrink to a face', icon: <Smile size={14} />, run: () => setBlob(win, true) }],
  accepts: ACCEPTS,
  Component: ChatWidget
}

export default ChatWidget
