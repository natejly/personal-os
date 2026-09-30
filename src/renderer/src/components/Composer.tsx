import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowUp, Square, Paperclip } from 'lucide-react'
import { useStore, useIsStreaming } from '../store'
import SmartTextarea from './SmartTextarea'

interface ComposerProps {
  conversationId?: string
  /** Rendered directly under the text box: where the model and effort controls live in a chat window. */
  footer?: ReactNode
  /** Tightens the padding, for a widget where vertical space is scarce. */
  compact?: boolean
}

export default function Composer({ conversationId, footer, compact = false }: ComposerProps): JSX.Element {
  const [text, setText] = useState('')
  const box = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const streaming = useIsStreaming(conversationId)
  const activeId = useStore((s) => conversationId ?? s.focusedConversationId)
  const uploadTarget = useStore((s) => s.sessions[conversationId ?? s.focusedConversationId ?? '']?.conversation.project_id ?? s.draftProjectId)
  const hasKey = useStore((s) => !!s.settings.apiKey)
  // One selector per action: a bare useStore() subscribes this textarea to every streamed token.
  const send = useStore((s) => s.send)
  const stop = useStore((s) => s.stop)
  const setSettingsOpen = useStore((s) => s.setSettingsOpen)
  const uploadDocuments = useStore((s) => s.uploadDocuments)

  useEffect(() => { box.current?.querySelector('textarea')?.focus() }, [activeId])

  /**
   * The draft is cleared optimistically and handed back if `send` refuses it. Typed text is never
   * dropped: a draft written since goes after the returned one. Mid-reply, `send` steers the live
   * run instead of refusing, so the composer stays open while the assistant works.
   */
  const submit = async (): Promise<void> => {
    if (!text.trim()) return
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
      <div className="composer" ref={box}>
        <input ref={fileRef} type="file" multiple hidden onChange={(e) => { if (e.target.files?.length) void uploadDocuments(e.target.files, uploadTarget); e.target.value = '' }} />
        <button className="icon-btn" title={uploadTarget ? "Add a document to this project" : "Add a personal document"} onClick={() => fileRef.current?.click()}><Paperclip size={16} /></button>
        <SmartTextarea
          kind="chat"
          variant="bare"
          rows={1}
          autoGrow
          maxHeight={240}
          minChars={8}
          value={text}
          onChange={setText}
          placeholder={streaming ? 'Steer the reply…' : 'Message… Tab accepts a suggestion'}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void submit() } }}
        />
        {streaming && !text.trim() ? (
          <button className="send stop" title="Stop" onClick={() => void stop(conversationId)}><Square size={14} /></button>
        ) : (
          <button className="send" title={streaming ? 'Steer the reply' : 'Send'} disabled={!text.trim()} onClick={() => void submit()}><ArrowUp size={16} /></button>
        )}
      </div>
      {footer && <div className="composer-footer">{footer}</div>}
    </div>
  )
}
