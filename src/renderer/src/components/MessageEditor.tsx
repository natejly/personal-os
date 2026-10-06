import { useEffect, useRef, useState } from 'react'
import type { Message } from '@shared/types'
import { editCut, useStore } from '../store'

/**
 * Inline editor for a sent user message. Resending hides this message and everything after it (nothing
 * is deleted) and answers the new text in the same run. Reads the store imperatively: a subscription
 * here would be a subscription inside every MessageView.
 */
export default function MessageEditor({ message, onClose }: { message: Message; onClose: () => void }): JSX.Element {
  const [text, setText] = useState(message.content)
  const [busy, setBusy] = useState(false)
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.focus()
    el.setSelectionRange(el.value.length, el.value.length)
  }, [])
  const submit = async (): Promise<void> => {
    const next = text.trim()
    if (!next || busy) return
    const st = useStore.getState()
    const { removed, ranTools } = editCut(st.sessions[message.conversation_id]?.conversation.messages ?? [], message.id)
    if ((removed > 2 || ranTools) && !window.confirm(`${removed} message${removed === 1 ? '' : 's'} will be hidden; actions already taken were not undone. Resend?`)) return
    setBusy(true)
    const ok = await st.editAndResend(message.id, next, message.conversation_id)
    setBusy(false)
    if (ok) onClose()
  }
  return (
    <div className="msg-editor">
      <textarea
        ref={ref}
        value={text}
        aria-label="Edit message"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') { e.preventDefault(); onClose() }
          else if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void submit() }
        }}
      />
      <div className="msg-editor-row">
        <span className="msg-editor-note">Replies after this message are hidden</span>
        <button type="button" className="ghost-btn" onClick={onClose}>Cancel</button>
        <button type="button" className="primary-btn" disabled={busy || !text.trim()} onClick={() => void submit()}>Resend</button>
      </div>
    </div>
  )
}
