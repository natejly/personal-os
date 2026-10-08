import { useEffect, useRef, useState } from 'react'
import { isClear } from '../../lib/workers'
import { Check, Clipboard, Copy, ExternalLink, Plus } from 'lucide-react'
import MessageView, { PendingUserMessage } from '../../components/Message'
import { quickAskMessage, quickAskTitle } from '../../lib/quickAsk'
import { stripNoReply } from '../../lib/noReply'
import { useChatFace, useConversation, useIsStreaming, usePendingSends, useStore, useStreamingMessageId } from '../../store'

/**
 * The quick-ask bar. The first Enter creates a new chat and streams its reply through the normal store, so tools,
 * approval cards and memory behave as in the main window; later Enters continue that chat. Esc closes, and the
 * window (main side) closes itself on blur.
 */
export default function QuickAsk(): JSX.Element {
  const [text, setText] = useState('')
  const [convId, setConvId] = useState<string | undefined>()
  const [clip, setClip] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')
  const root = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const scroll = useRef<HTMLDivElement>(null)
  const theme = useStore((s) => s.settings.theme)
  const ready = useStore((s) => s.ready)
  const backendError = useStore((s) => s.backendError)
  const convo = useConversation(convId)
  const face = useChatFace(convo)
  const streaming = useIsStreaming(convId)
  const streamingId = useStreamingMessageId(convId)
  const pending = usePendingSends(convId)

  useEffect(() => { void useStore.getState().init() }, [])
  useEffect(() => { document.documentElement.dataset.theme = theme }, [theme])

  // The window is as tall as its content: main clamps the height it is told.
  useEffect(() => {
    const el = root.current
    if (!el) return
    const ro = new ResizeObserver(() => void window.os.quickAsk.resize(el.scrollHeight))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const msgs = convo?.messages ?? []
  const last = msgs[msgs.length - 1]
  const lastLen = last?.content.length ?? 0
  useEffect(() => { scroll.current?.scrollTo({ top: scroll.current.scrollHeight }) }, [lastLen, msgs.length, pending.length])
  // The textarea grows with its lines, up to a few.
  useEffect(() => {
    const el = input.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 120)}px`
  }, [text])

  const toggleClip = async (): Promise<void> => {
    if (clip !== null) return setClip(null)
    const t = (await window.os.quickAsk.clipboard()).trim()
    if (t) setClip(t)
    else setError('The clipboard has no text.')
  }

  const submit = async (): Promise<void> => {
    const prompt = text.trim()
    if (!prompt || !ready || backendError) return
    setError('')
    const app = useStore.getState()
    let id = convId
    if (!id) {
      const c = await app.createConversation(null)
      if (!c) return setError('Could not start a chat.')
      id = c.id
      setConvId(id)
      void app.renameChat(id, quickAskTitle(prompt))
    }
    const body = quickAskMessage(prompt, clip)
    setText('')
    setClip(null)
    if (!(await app.send(body, id))) setText(prompt)
  }

  const reset = (): void => {
    setConvId(undefined)
    setText('')
    setClip(null)
    setError('')
    input.current?.focus()
  }

  const lastReply = stripNoReply([...msgs].reverse().find((m) => m.role === 'assistant')?.content)
  const copy = (): void => {
    void navigator.clipboard.writeText(lastReply)
    setCopied(true)
    setTimeout(() => setCopied(false), 1200)
  }

  return (
    <div ref={root} className="quick-ask" style={{ padding: 12, boxSizing: 'border-box', display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 640 }}>
      <div className="quick-ask-row" style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
        <textarea
          ref={input}
          autoFocus
          rows={1}
          value={text}
          placeholder={convId ? 'Follow up…' : 'Ask anything. Enter to send, Esc to close.'}
          aria-label="Ask"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') window.os.closeSelf()
            else if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void submit() }
          }}
          style={{ flex: 1, resize: 'none', font: 'inherit', overflow: 'auto' }}
        />
        <button className={clip !== null ? 'ctx-chip on' : 'ctx-chip'} aria-pressed={clip !== null} title="Attach the text on your clipboard as context" onClick={() => void toggleClip()}>
          <Clipboard size={11} /> Clipboard
        </button>
      </div>
      {clip !== null && <small className="widget-sub" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>Clipboard: {clip.replace(/\s+/g, ' ').slice(0, 120)}</small>}
      {(error || backendError) && <small role="alert" style={{ color: 'var(--danger, #e5484d)' }}>{error || backendError}</small>}
      {convId && (
        <>
          <div ref={scroll} className="messages" style={{ flex: 'none', maxHeight: 460, overflowY: 'auto' }}>
            <div className="widget" style={{ height: 'auto' }}>
              <div className="messages-inner">
                {msgs.map((m) => isClear(m) ? <div key={m.id} className="day-divider" role="separator">Context cleared</div> : <MessageView key={m.id} message={m} face={face} streaming={streaming && streamingId === m.id} last={m.id === last?.id} />)}
                {pending.map((p) => <PendingUserMessage key={p.key} text={p.text} attachments={p.attachments} />)}
              </div>
            </div>
          </div>
          <div className="quick-ask-actions" style={{ display: 'flex', gap: 6 }}>
            <button className="ghost-btn" onClick={() => void window.os.quickAsk.openChat(convId)}><ExternalLink size={12} /> Open in chat</button>
            <button className="ghost-btn" disabled={!lastReply} onClick={copy}>{copied ? <Check size={12} /> : <Copy size={12} />} Copy</button>
            <button className="ghost-btn" onClick={reset}><Plus size={12} /> New</button>
          </div>
        </>
      )}
    </div>
  )
}
