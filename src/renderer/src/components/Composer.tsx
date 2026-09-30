import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowUp, Square, Paperclip } from 'lucide-react'
import { useStore, useIsStreaming } from '../store'

interface ComposerProps {
  conversationId?: string
  /** Rendered directly under the text box: where the model and effort controls live in a chat window. */
  footer?: ReactNode
  /** Drops the disclaimer line and tightens the padding, for a widget where vertical space is scarce. */
  compact?: boolean
}

export default function Composer({ conversationId, footer, compact = false }: ComposerProps): JSX.Element {
  const [text, setText] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const streaming = useIsStreaming(conversationId)
  const activeId = useStore((s) => conversationId ?? s.focusedConversationId)
  const uploadTarget = useStore((s) => s.sessions[conversationId ?? s.focusedConversationId ?? '']?.conversation.project_id ?? s.draftProjectId)
  const hasKey = useStore((s) => !!s.settings.apiKey)
  const { send, stop, setSettingsOpen, uploadDocuments } = useStore()

  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`
  }, [text])
  useEffect(() => { ref.current?.focus() }, [activeId])

  /**
   * The draft is cleared optimistically and handed back if `send` refuses it — another window can
   * have a reply in flight for this conversation, which the backend answers with a 409 and nothing
   * persisted. Typed text is never dropped: a draft written since goes after the returned one.
   */
  const submit = async (): Promise<void> => {
    if (streaming || !text.trim()) return
    const t = text
    setText('')
    const ok = await send(t, conversationId).catch(() => false)
    if (!ok) setText((cur) => (cur.trim() ? `${t}\n\n${cur}` : t))
  }

  return (
    <div className={compact ? 'composer-wrap compact' : 'composer-wrap'}>
      {!hasKey && (
        <div className="notice">No LiteLLM key set. <button className="link" onClick={() => setSettingsOpen(true)}>Open settings</button></div>
      )}
      <div className="composer">
        <input ref={fileRef} type="file" multiple hidden onChange={(e) => { if (e.target.files?.length) void uploadDocuments(e.target.files, uploadTarget); e.target.value = '' }} />
        <button className="icon-btn" title={uploadTarget ? "Add a document to this project" : "Add a personal document"} onClick={() => fileRef.current?.click()}><Paperclip size={16} /></button>
        <textarea ref={ref} rows={1} value={text} placeholder="Message… (Enter to send, Shift+Enter for newline)"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void submit() } }} />
        {streaming ? (
          <button className="send stop" title="Stop" onClick={() => void stop(conversationId)}><Square size={14} /></button>
        ) : (
          <button className="send" title="Send" disabled={!text.trim()} onClick={() => void submit()}><ArrowUp size={16} /></button>
        )}
      </div>
      {footer && <div className="composer-footer">{footer}</div>}
      {!compact && <p className="composer-hint">Models can make mistakes. Routed through LiteLLM.</p>}
    </div>
  )
}
