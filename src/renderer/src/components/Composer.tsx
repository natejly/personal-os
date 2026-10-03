import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowUp, Square, Paperclip } from 'lucide-react'
import PlanModeToggle from './PlanModeToggle'
import SkipPermissionsToggle from './SkipPermissionsToggle'
import { uploadContextNote } from '../lib/uploadNote'
import { useStore, useIsStreaming } from '../store'
import SmartTextarea from './SmartTextarea'
import { useOnboarding } from './onboarding/onboardingStore'
import { COMPOSER_INSERT_EVENT, appendDraft, type ComposerInsertDetail } from '../lib/composerInsert'

interface ComposerProps {
  conversationId?: string
  /** Rendered directly under the text box: where the model and effort controls live in a chat window. */
  footer?: ReactNode
  /** Tightens the padding, for a widget where vertical space is scarce. */
  compact?: boolean
  /** Overrides the store's `send`, for a composer that is not a plain chat — the ⌘I page agent. */
  onSend?: (text: string) => Promise<boolean>
  placeholder?: string
}

export default function Composer({ conversationId, footer, compact = false, onSend, placeholder }: ComposerProps): JSX.Element {
  const [text, setText] = useState('')
  const box = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const streaming = useIsStreaming(conversationId)
  const activeId = useStore((s) => conversationId ?? s.focusedConversationId)
  const uploadTarget = useStore((s) => s.sessions[conversationId ?? s.focusedConversationId ?? '']?.conversation.project_id ?? s.draftProjectId)
  const hasKey = useStore((s) => !!s.settings.apiKeySet || /^https?:\/\/(localhost|127\.0\.0\.1)[:/]/.test(s.settings.baseUrl ?? ''))
  // A local endpoint (Ollama, a local proxy) needs no key, so it is not "unfinished".
  // One selector per action: a bare useStore() subscribes this textarea to every streamed token.
  const send = useStore((s) => s.send)
  const stop = useStore((s) => s.stop)
  const openWizard = useOnboarding((s) => s.openWizard)
  const uploadDocuments = useStore((s) => s.uploadDocuments)
  const noteUntrustedUpload = useStore((s) => s.noteUntrustedUpload)

  useEffect(() => { box.current?.querySelector('textarea')?.focus() }, [activeId])

  // A tool card's slot chip asks for text in the composer of the conversation being looked at.
  useEffect(() => {
    const onInsert = (e: Event): void => {
      if ((conversationId ?? activeId) !== useStore.getState().focusedConversationId) return
      const t = (e as CustomEvent<ComposerInsertDetail>).detail?.text
      if (!t) return
      setText((cur) => appendDraft(cur, t))
      box.current?.querySelector('textarea')?.focus()
    }
    window.addEventListener(COMPOSER_INSERT_EVENT, onInsert)
    return () => window.removeEventListener(COMPOSER_INSERT_EVENT, onInsert)
  }, [activeId, conversationId])

  /**
   * The draft is cleared optimistically and handed back if `send` refuses it. Typed text is never
   * dropped: a draft written since goes after the returned one. Mid-reply, `send` steers the live
   * run instead of refusing, so the composer stays open while the assistant works.
   */
  const attach = async (files: FileList | File[]): Promise<void> => {
    const list = Array.from(files)
    if (!list.length) return
    const saved = await uploadDocuments(list, uploadTarget)
    if (!saved.length) return
    const real = conversationId && conversationId !== '\u0000page-agent' ? conversationId : undefined
    await noteUntrustedUpload(real, onSend ? 'page' : 'draft').catch((e: unknown) => {
      useStore.getState().toast((e as Error).message, 'error')
    })
    const note = uploadContextNote(saved)
    setText((cur) => (cur.trim() ? `${cur}\n\n${note}` : note))
  }

  const submit = async (): Promise<void> => {
    if (!text.trim()) return
    const t = text
    setText('')
    const ok = await (onSend ? onSend(t) : send(t, conversationId)).catch(() => false)
    if (!ok) setText((cur) => (cur.trim() ? `${t}\n\n${cur}` : t))
  }

  return (
    <div className={compact ? 'composer-wrap compact' : 'composer-wrap'}>
      {!hasKey && (
        <div className="notice">Finish setup to start chatting. <button className="link" onClick={openWizard}>Finish setup</button></div>
      )}
      <div
        className="composer"
        ref={box}
        onDragOver={(e) => { if (e.dataTransfer.types.includes('Files')) e.preventDefault() }}
        onDrop={(e) => { if (!e.dataTransfer.files.length) return; e.preventDefault(); void attach(e.dataTransfer.files) }}
      >
        <input ref={fileRef} type="file" multiple hidden onChange={(e) => { if (e.target.files?.length) void attach(e.target.files); e.target.value = '' }} />
        <button className="icon-btn" title="Add files to this chat" onClick={() => fileRef.current?.click()}><Paperclip size={16} /></button>
        <SmartTextarea
          kind="chat"
          variant="bare"
          rows={1}
          autoGrow
          maxHeight={240}
          minChars={8}
          value={text}
          onChange={setText}
          placeholder={streaming ? 'Steer the reply…' : placeholder}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void submit() } }}
        />
        <div className="composer-actions">
          {streaming && (
            <button className="send stop" title="Stop" aria-label="Stop" onClick={() => void stop(conversationId)}><Square size={14} /></button>
          )}
          {(!streaming || text.trim()) && (
            <button className="send" title={streaming ? 'Steer the reply' : 'Send'} aria-label={streaming ? 'Steer the reply' : 'Send'} disabled={!text.trim()} onClick={() => void submit()}><ArrowUp size={16} /></button>
          )}
        </div>
      </div>
      {/* Always rendered: the plan-mode toggle belongs to every composer, and it binds ⌘⇧P itself —
          only for the focused conversation, so several mounted chat widgets do not all cycle at once. */}
      <div className="composer-footer">
        <PlanModeToggle conversationId={conversationId} />
        <SkipPermissionsToggle conversationId={conversationId} />
        {footer}
      </div>
    </div>
  )
}
