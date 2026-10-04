import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowUp, Square, Paperclip, Loader2 } from 'lucide-react'
import PlanModeToggle from './PlanModeToggle'
import SkipPermissionsToggle from './SkipPermissionsToggle'
import { uploadNote } from '../lib/uploadNote'
import { useStore, useIsStreaming, useIsStopping } from '../store'
import SmartTextarea from './SmartTextarea'
import { useOnboarding } from './onboarding/onboardingStore'
import { COMPOSER_INSERT_EVENT } from '../lib/composerInsert'
import { classifyPaste, messageCharLimit } from '../lib/messageLimit'
import { appendToDraft, clearRedirect, composerKey, dropDraft, getDraft, moveDraft, restoreDraft, useDraft } from '../lib/drafts'
import { enqueue, enterAction, removeQueued, requeueFront, sendNext, updateQueue, type QueuedItem } from '../lib/followQueue'
import QueueTray from './QueueTray'

interface ComposerProps {
  conversationId?: string
  /** Rendered directly under the text box: where the model and effort controls live in a chat window. */
  footer?: ReactNode
  /** Tightens the padding, for a widget where vertical space is scarce. */
  compact?: boolean
  /** Overrides the store's `send`, for a composer that is not a plain chat — the ⌘I page agent. */
  onSend?: (text: string) => Promise<boolean>
  placeholder?: string
  /** Where the unsent text lives (lib/drafts.ts); derived from the conversation when not given. */
  draftKey?: string
}

export default function Composer({ conversationId, footer, compact = false, onSend, placeholder, draftKey }: ComposerProps): JSX.Element {
  const box = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const streaming = useIsStreaming(conversationId)
  const stopping = useIsStopping(conversationId)
  const activeId = useStore((s) => conversationId ?? s.focusedConversationId)
  const draftProjectId = useStore((s) => s.draftProjectId)
  // The draft is read by key, so switching chats or views shows each one's own text and a refused
  // send can be handed back to the chat it came from, not whichever is showing by then.
  const key = draftKey ?? composerKey({ conversationId, page: !!onSend, focusedId: activeId, draftProjectId })
  const [text, setText] = useDraft(key)
  const uploadTarget = useStore((s) => s.sessions[conversationId ?? s.focusedConversationId ?? '']?.conversation.project_id ?? s.draftProjectId)
  const hasKey = useStore((s) => !!s.settings.apiKeySet || /^https?:\/\/(localhost|127\.0\.0\.1)[:/]/.test(s.settings.baseUrl ?? ''))
  // A local endpoint (Ollama, a local proxy) needs no key, so it is not "unfinished".
  // One selector per action: a bare useStore() subscribes this textarea to every streamed token.
  const send = useStore((s) => s.send)
  const stop = useStore((s) => s.stop)
  const openWizard = useOnboarding((s) => s.openWizard)
  const uploadDocuments = useStore((s) => s.uploadDocuments)
  const noteUntrustedUpload = useStore((s) => s.noteUntrustedUpload)
  // The follow-up queue is a chat's: the page agent panel (`onSend`) keeps steering on Enter.
  const queueId = !onSend && activeId ? activeId : null
  const cardPending = useStore((s) => !!queueId && (s.sessions[queueId]?.pendingApprovals ?? 0) > 0)
  const desk = useStore((s) => !!queueId && s.desks.some((d) => d.conversation_id === queueId))
  /** A steer that would decline an open card, waiting on the user's yes. `item` when it came from the tray. */
  const [confirm, setConfirm] = useState<{ item?: QueuedItem } | null>(null)
  useEffect(() => { if (!cardPending) setConfirm(null) }, [cardPending])

  useEffect(() => { box.current?.querySelector('textarea')?.focus() }, [activeId])

  /** Stop, then hand the keyboard back: the button that was pressed is about to be replaced or disabled. */
  const halt = (): void => {
    void stop(conversationId).finally(() => box.current?.querySelector('textarea')?.focus())
  }

  // A tool card's slot chip put its text in the drafts store already; the composer of the
  // conversation being looked at only takes focus so the next keystroke lands after it.
  useEffect(() => {
    if (onSend) return
    const onInsert = (): void => {
      if ((conversationId ?? activeId) !== useStore.getState().focusedConversationId) return
      box.current?.querySelector('textarea')?.focus()
    }
    window.addEventListener(COMPOSER_INSERT_EVENT, onInsert)
    return () => window.removeEventListener(COMPOSER_INSERT_EVENT, onInsert)
  }, [activeId, conversationId, onSend])

  /** The key as it stands now: a send from the not-yet-created chat ends with the created one focused. */
  const keyNow = (): string => {
    const s = useStore.getState()
    return draftKey ?? composerKey({ conversationId, page: !!onSend, focusedId: s.focusedConversationId, draftProjectId: s.draftProjectId })
  }

  /**
   * The upload note goes to the draft this composer showed when the files were picked, which may no
   * longer be the one on screen by the time the upload ends. A file with no readable text gets no
   * note (uploadNote.ts). A draft with no row yet remembers the upload, so the send still creates
   * the chat marked untrusted, even after a relaunch.
   */
  const attach = async (files: FileList | File[]): Promise<void> => {
    const list = Array.from(files)
    if (!list.length) return
    const k0 = key
    const saved = await uploadDocuments(list, uploadTarget)
    if (!saved.length) return
    const real = conversationId && conversationId !== '\u0000page-agent' ? conversationId : undefined
    await noteUntrustedUpload(real, onSend ? 'page' : 'draft').catch((e: unknown) => {
      useStore.getState().toast((e as Error).message, 'error')
    })
    const { note } = uploadNote(saved)
    if (note) appendToDraft(k0, note, { paragraph: true, taint: real ? undefined : 'upload' })
  }

  /**
   * Paste: an image or file on the clipboard goes through `attach`, as a drop does; a text block
   * longer than PASTE_AS_FILE_CHARS becomes a .txt attachment (its note lands in the draft); text
   * that would push the draft past the message bound is refused with the notice. Everything else
   * pastes natively. A text payload always wins over files (spreadsheets add an image rendition).
   */
  const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>): void => {
    const ta = e.currentTarget
    const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '')
    const files = Array.from(e.clipboardData.files).map((f, i) =>
      // A clipboard image arrives as a bare "image.png"; a name per paste keeps two uploads apart.
      /^image\.\w+$/i.test(f.name) ? new File([f], `pasted-image-${stamp}${i ? `-${i + 1}` : ''}.${f.name.split('.').pop()}`, { type: f.type }) : f)
    const action = classifyPaste({
      text: e.clipboardData.getData('text/plain'), files, draftLength: text.length,
      selectionLength: ta.selectionEnd - ta.selectionStart, limit: messageCharLimit(useStore.getState().settings.contextWindow), stamp
    })
    if (action.kind === 'native') return
    e.preventDefault()
    if (action.kind === 'block') useStore.getState().toast(action.notice, 'error')
    else if (action.kind === 'files') void attach(action.files)
    else {
      useStore.getState().toast(`Pasted text was long, so it is attached as ${action.name} instead of inline.`, 'info')
      void attach([new File([action.text], action.name, { type: 'text/plain' })])
    }
  }

  /**
   * The draft is cleared optimistically and handed back if `send` refuses it. Typed text is never
   * dropped: a draft written since goes after the returned one, under the key the composer resolves
   * at that moment (a chat created by this send included). Mid-reply, `send` steers the live run
   * instead of refusing, so the composer stays open while the assistant works.
   */
  const deliver = async (): Promise<void> => {
    const k0 = key
    const t = getDraft(k0)?.text ?? ''
    if (!t.trim()) return
    const entry = getDraft(k0)
    // The untrusted mark an upload left on a row-less draft is consumed by the next send; after a
    // relaunch only the draft remembers it, so it is re-armed here before the send reads it.
    if (entry?.taint && useStore.getState().uploadTaintTarget === null) {
      useStore.setState({ uploadTaintTarget: onSend ? 'page' : 'draft', uploadTaintSource: entry.taint })
    }
    clearRedirect(k0)
    dropDraft(k0)
    const ok = await (onSend ? onSend(t) : send(t, conversationId)).catch(() => false)
    const k1 = keyNow()
    // The new chat has its row now: anything typed while it was being made follows it.
    if (k0.startsWith('new:') && k1.startsWith('c:')) moveDraft(k0, k1)
    if (!ok) restoreDraft(k1, t)
  }

  /** A queued item sent ahead of its turn: a steer while the reply runs. Refused, it goes back in front. */
  const deliverItem = async (item: QueuedItem): Promise<void> => {
    if (!queueId) return
    updateQueue(queueId, (q) => removeQueued(q, item.id))
    const ok = await send(item.text, conversationId ?? queueId).catch(() => false)
    if (!ok) updateQueue(queueId, (q) => requeueFront(q, item))
  }

  /**
   * Enter (and the send button) while a reply runs queues the text as the next turn; ⌘Enter steers the
   * live reply. A steer while a card waits declines that card, so it asks first (lib/followQueue.ts).
   */
  const submit = (mod = false): void => {
    if (!text.trim()) return
    const action = queueId ? enterAction({ busy: streaming, mod, cardPending, desk }) : 'send'
    if (action === 'queue' && queueId) {
      updateQueue(queueId, (q) => enqueue(q, text, crypto.randomUUID()))
      clearRedirect(key)
      dropDraft(key)
    } else if (action === 'confirm-steer') setConfirm({})
    else void deliver()
  }

  const sendNow = (item: QueuedItem): void => {
    if (streaming && cardPending && !desk) setConfirm({ item })
    else void deliverItem(item)
  }

  const resume = (): void => {
    if (!queueId) return
    updateQueue(queueId, (q) => ({ ...q, paused: false }))
    // Idle, nothing will finish to pull the next one: it goes now.
    if (!streaming) sendNext(queueId, {}, (t) => send(t, conversationId ?? queueId))
  }

  const confirmSteer = (): void => {
    const c = confirm
    setConfirm(null)
    if (c?.item) void deliverItem(c.item)
    else void deliver()
  }

  const queueInstead = (): void => {
    const c = confirm
    setConfirm(null)
    if (!c?.item) submit(false)
  }

  const sendLabel = !streaming ? 'Send' : queueId ? 'Queue a follow-up (⌘↵ steers now)' : 'Steer the reply'

  return (
    <div className={compact ? 'composer-wrap compact' : 'composer-wrap'}>
      {!hasKey && (
        <div className="notice">Finish setup to start chatting. <button className="link" onClick={openWizard}>Finish setup</button></div>
      )}
      {queueId && (
        <QueueTray conversationId={queueId} busy={streaming} onSendNow={sendNow} onResume={resume}
          onEdit={(t) => { appendToDraft(key, t); box.current?.querySelector('textarea')?.focus() }} />
      )}
      {confirm && (
        <div className="notice queue-confirm" role="alertdialog" aria-label="Decline the open card?">
          Sending now declines the open card and tells the assistant why. To change the card instead (add a cc, move a time), edit it in place.{' '}
          <button className="link danger" onClick={confirmSteer}>Decline and send</button>{' '}
          {!confirm.item && <><button className="link" onClick={queueInstead}>Queue instead</button>{' '}</>}
          <button className="link" onClick={() => setConfirm(null)}>Cancel</button>
        </div>
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
          onPaste={onPaste}
          placeholder={streaming ? (queueId ? 'Queue a follow-up… ⌘↵ to steer now' : 'Steer the reply…') : placeholder}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(e.metaKey || e.ctrlKey) }
            // Escape closes the decline prompt first, then ends the reply; while an input method is composing it belongs to the method.
            else if (e.key === 'Escape' && confirm && !e.nativeEvent.isComposing) { e.preventDefault(); setConfirm(null) }
            else if (e.key === 'Escape' && streaming && !e.nativeEvent.isComposing) { e.preventDefault(); halt() }
          }}
        />
        <div className="composer-actions">
          {streaming && (
            <button className="send stop" title={stopping ? 'Stopping…' : 'Stop (Esc)'} aria-label={stopping ? 'Stopping' : 'Stop'} aria-busy={stopping} disabled={stopping} onClick={halt}>
              {stopping ? <Loader2 size={14} className="spin" /> : <Square size={14} />}
            </button>
          )}
          {(!streaming || text.trim()) && (
            <button className="send" title={sendLabel} aria-label={sendLabel} disabled={!text.trim()} onClick={() => submit()}><ArrowUp size={16} /></button>
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
