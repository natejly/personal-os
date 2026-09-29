import { useEffect, useRef, useState } from 'react'
import { ArrowUp, Square, Paperclip } from 'lucide-react'
import { useStore } from '../store'

export default function Composer(): JSX.Element {
  const [text, setText] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const streaming = useStore((s) => s.streaming)
  const activeId = useStore((s) => s.activeId)
  const uploadTarget = useStore((s) => s.active?.project_id ?? s.draftProjectId)
  const hasKey = useStore((s) => !!s.settings.apiKey)
  const { send, stop, setSettingsOpen, uploadDocuments } = useStore()

  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`
  }, [text])
  useEffect(() => { ref.current?.focus() }, [activeId])

  const submit = (): void => {
    if (streaming || !text.trim()) return
    const t = text
    setText('')
    void send(t)
  }

  return (
    <div className="composer-wrap">
      {!hasKey && (
        <div className="notice">No LiteLLM key set. <button className="link" onClick={() => setSettingsOpen(true)}>Open settings</button></div>
      )}
      <div className="composer">
        <input ref={fileRef} type="file" multiple hidden onChange={(e) => { if (e.target.files?.length) void uploadDocuments(e.target.files, uploadTarget); e.target.value = '' }} />
        <button className="icon-btn" title={uploadTarget ? "Add a document to this project" : "Add a personal document"} onClick={() => fileRef.current?.click()}><Paperclip size={16} /></button>
        <textarea ref={ref} rows={1} value={text} placeholder="Message… (Enter to send, Shift+Enter for newline)"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit() } }} />
        {streaming ? (
          <button className="send stop" title="Stop" onClick={() => void stop()}><Square size={14} /></button>
        ) : (
          <button className="send" title="Send" disabled={!text.trim()} onClick={submit}><ArrowUp size={16} /></button>
        )}
      </div>
      <p className="composer-hint">Models can make mistakes. Routed through LiteLLM.</p>
    </div>
  )
}
