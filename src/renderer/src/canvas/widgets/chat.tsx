import { useEffect, useRef, useState, type DragEvent } from 'react'
import { MessageSquare, RefreshCw } from 'lucide-react'
import type { DragKind, DragPayload } from '@shared/types'
import MessageView from '../../components/Message'
import Composer from '../../components/Composer'
import { useConversation, useIsStreaming, useStore, useStreamingMessageId } from '../../store'
import { useDropTarget } from '../dnd'
import type { WidgetDef, WidgetProps } from '../registry'
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

const names = (files: FileList): string => [...files].map((f) => f.name).join(', ')

/** Contract §6 keeps this one survivor of the chat header: without it a window cannot be re-modelled. */
const ModelPicker = ({ convId }: { convId: string }): JSX.Element => {
  const models = useStore((s) => s.models)
  const model = useStore((s) => s.sessions[convId]?.conversation.model ?? s.settings.defaultModel)
  const setChatModel = useStore((s) => s.setChatModel)
  const options = models.some((m) => m.id === model) ? models : [{ id: model }, ...models]
  return (
    <select className="widget-input" style={{ width: 'auto', maxWidth: 220 }} value={model} title="Model"
      onChange={(e) => void setChatModel(e.target.value, convId)}>
      {options.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
    </select>
  )
}

function ChatWidget({ window: win, live, onTitle }: WidgetProps): JSX.Element {
  const convId = win.ref_id ?? ''
  const root = useRef<HTMLDivElement>(null)
  const scroll = useRef<HTMLDivElement>(null)
  const titled = useRef(win.title)
  const [stick, setStick] = useState(true)
  const [gone, setGone] = useState(false)
  const convo = useConversation(convId)
  const streaming = useIsStreaming(convId)
  const streamingId = useStreamingMessageId(convId)
  const { status } = useRingStatus(convId)
  const regenerate = useStore((s) => s.regenerate)

  // `attachSession`, not `openSession`: a window opened over a reply already in flight adopts that run
  // from its own seq, so it paints amber at once instead of waiting for the next one.
  useEffect(() => {
    if (!convId) return
    let alive = true
    setGone(false)
    void useStore.getState().attachSession(convId).catch(() => { if (alive) setGone(true) })
    return () => { alive = false }
  }, [convId])

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
      await app.uploadDocuments(files, convo?.project_id ?? null)
      draft(`I just uploaded ${names(files)}. Find them with search_documents and read them before answering.`)
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
  if (gone) return <div className="widget-error">That conversation is gone.</div>

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
      {/* §6: this bar holds ModelPicker and nothing else — the status ring is the title bar's. */}
      <div className="widget-bar">
        <ModelPicker convId={convId} />
      </div>
      <div className="messages" ref={scroll} onScroll={onScroll}>
        <div className="messages-inner">
          {msgs.map((m) => <MessageView key={m.id} message={m} streaming={streaming && streamingId === m.id} />)}
          {!streaming && last?.role === 'assistant' && (
            <div className="regen-row">
              <button className="ghost-btn" onClick={() => void regenerate(convId)}><RefreshCw size={13} /> Regenerate</button>
            </div>
          )}
          {!msgs.length && <p className="widget-sub">Nothing here yet. Say something.</p>}
        </div>
      </div>
      <Composer conversationId={convId} />
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
