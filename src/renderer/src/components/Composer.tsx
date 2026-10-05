import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { Command, Skill } from '@shared/types'
import { SKILL_PRESETS, type SkillPreset } from '@shared/skillPresets'
import { api } from '../lib/api'
import CaretMenu from '../features/notes/CaretMenu'
import { slashMenuKey } from '../features/notes/slash'
import { clientCommand, skillSlug, slashItems, suggestSkills } from '../lib/slashCommands'
import { ArrowUp, Square, Paperclip, Loader2, EyeOff, Sparkles, Download } from 'lucide-react'
import PlanModeToggle from './PlanModeToggle'
import SkipPermissionsToggle from './SkipPermissionsToggle'
import WorkingFolder from './WorkingFolder'
import { uploadNote } from '../lib/uploadNote'
import { hasModelKey } from '../lib/modelLabel'
import { PAGE_AGENT_DRAFT, useStore, useIsStreaming, useIsStopping } from '../store'
import SmartTextarea from './SmartTextarea'
import MicButton from './MicButton'
import { dictationText } from '../features/docrec/dictation'
import { useOnboarding } from './onboarding/onboardingStore'
import { COMPOSER_INSERT_EVENT, type ComposerInsertDetail } from '../lib/composerInsert'
import { classifyPaste, messageCharLimit } from '../lib/messageLimit'
import { compactNow } from '../lib/compact'
import { appendToDraft, clearRedirect, composerKey, dropDraft, getDraft, moveDraft, restoreDraft, useDraft } from '../lib/drafts'
import { promptList, recallKey, step, type Recall } from '../lib/promptHistory'
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

export default function Composer({ conversationId, footer, compact = false, onSend, placeholder = 'Ask anything', draftKey }: ComposerProps): JSX.Element {
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
  const hasKey = useStore((s) => hasModelKey(s.settings))
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
  // Private is fixed when the chat is created, so it is a switch only on a draft and a label after.
  const chatPrivate = useStore((s) => (activeId ? !!s.sessions[activeId]?.conversation.settings.private : s.draftPrivate))
  const setChatSettings = useStore((s) => s.setChatSettings)

  useEffect(() => { box.current?.querySelector('textarea')?.focus() }, [activeId])

  // The '/' menu: built-ins (lib/slashCommands.ts) beside the saved commands, read once per mount. Picking one
  // types `/name `; a built-in the UI handles runs on send, the rest the backend fills when the turn goes to the
  // model (commands.expand), so nothing runs until the user sends.
  const [commands, setCommands] = useState<Command[]>([])
  useEffect(() => { api.commands.list().then(setCommands).catch(() => undefined) }, [])
  const skills = useStore((s) => s.skills)
  const [slashActive, setSlashActive] = useState(0)
  const [slashClosedAt, setSlashClosedAt] = useState<string | null>(null) // Esc hides the menu until the text changes
  const slash = slashClosedAt === text ? null : slashItems(text, commands, skills)
  useEffect(() => setSlashActive(0), [text])

  // Skills that fit what is being typed: the user's approved ones to use now, or, with none of those fitting,
  // one popular skill to import (it lands as a candidate). The page agent panel has no skills of its own.
  const suggested = useMemo(() => (onSend ? [] : suggestSkills(text, skills.filter((s) => s.status === 'approved'))), [text, skills, onSend])
  const presetHint = useMemo(() => (onSend || suggested.length ? [] : suggestSkills(text,
    SKILL_PRESETS.filter((p) => !skills.some((s) => skillSlug(s.name) === p.name)).map((p) => ({ ...p, description: p.blurb })), 1)),
    [text, skills, onSend, suggested.length])
  const [importingPreset, setImportingPreset] = useState<string | null>(null)
  const useSkill = (s: Skill): void => {
    setText(`/skill ${skillSlug(s.name)} ${text}`)
    box.current?.querySelector('textarea')?.focus()
  }
  const importPreset = async (p: SkillPreset): Promise<void> => {
    const { toast, refreshSkills } = useStore.getState()
    setImportingPreset(p.name)
    try {
      const r = await api.skills.importMd({ url: p.url })
      await refreshSkills()
      toast(`Imported “${r.skill.name}” as a candidate. Approve it under Library → Skills before it is used.`, 'info',
        { label: 'Open Skills', run: () => { useStore.getState().setLibraryTab('skills'); useStore.getState().setView('library') } })
    } catch (e) {
      toast(`Could not import ${p.name}: ${(e as Error).message}`, 'error')
    } finally {
      setImportingPreset(null)
    }
  }

  /** A built-in the UI handles itself (/compact, /skills, /commands, /plan). The draft is dropped once it has run. */
  const runClient = async ({ name, args }: { name: string; args: string }, k0: string): Promise<void> => {
    const s = useStore.getState()
    if (name === 'compact') {
      // "/compact [focus]" summarizes this chat's history instead of sending a message.
      if (!activeId) return s.toast('Nothing to compact yet: this chat has no history.', 'info')
      try {
        const res = await compactNow(activeId, args)
        dropDraft(k0)
        s.toast(res.compacted ? 'Earlier messages were summarized.' : 'Nothing old enough to compact yet.', 'info')
      } catch (e) {
        s.toast(`Could not compact: ${(e as Error).message}`, 'error')
      }
      return
    }
    dropDraft(k0)
    if (name === 'skills' || name === 'commands') {
      s.setLibraryTab(name === 'skills' ? 'skills' : 'automations')
      s.setView('library')
    } else if (name === 'plan') {
      const modes = ['off', 'auto', 'always'] as const
      type Mode = (typeof modes)[number]
      const cur: Mode = (activeId ? s.sessions[activeId]?.conversation.settings.planMode : s.draftChatSettings.planMode) ?? s.settings.planMode ?? 'off'
      const want: Mode = (modes as readonly string[]).includes(args) ? (args as Mode) : modes[(modes.indexOf(cur) + 1) % modes.length]
      await setChatSettings({ planMode: want }, conversationId).catch((e: unknown) => s.toast((e as Error).message, 'error'))
      s.toast(`Plan mode: ${want}`, 'info')
    }
  }

  /** Up/Down through this chat's earlier prompts (lib/promptHistory.ts); null when not recalling. */
  const recall = useRef<Recall | null>(null)
  useEffect(() => { recall.current = null }, [key])

  /** Handles the key when it is a recall step; returns whether it did. */
  const onRecallKey = (e: React.KeyboardEvent<HTMLTextAreaElement>): boolean => {
    if (e.nativeEvent.isComposing) return false
    const ta = e.currentTarget
    const act = recallKey(e.key, { value: text, selStart: ta.selectionStart, selEnd: ta.selectionEnd, streaming, modified: e.shiftKey || e.altKey || e.metaKey || e.ctrlKey }, recall.current)
    if (act === 'exit') { recall.current = null; e.preventDefault(); return true }
    if (!act) return false
    const s = useStore.getState()
    const next = step(promptList(s.sessions[conversationId ?? s.focusedConversationId ?? '']?.conversation.messages ?? []), recall.current, text, act)
    if (!next) return false
    e.preventDefault()
    recall.current = next.recall
    setText(next.text)
    return true
  }

  /** Stop, then hand the keyboard back: the button that was pressed is about to be replaced or disabled. */
  const halt = (): void => {
    void stop(conversationId).finally(() => box.current?.querySelector('textarea')?.focus())
  }

  // A tool card's slot chip put its text in the drafts store already; the composer holding that
  // draft only takes focus so the next keystroke lands after it.
  useEffect(() => {
    const onInsert = (e: Event): void => {
      if ((e as CustomEvent<ComposerInsertDetail>).detail?.key !== key) return
      box.current?.querySelector('textarea')?.focus()
    }
    window.addEventListener(COMPOSER_INSERT_EVENT, onInsert)
    return () => window.removeEventListener(COMPOSER_INSERT_EVENT, onInsert)
  }, [key])

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
    // The chat on screen when the files were picked, as `key` resolves it: the main view passes no id,
    // and a mark left for "the next send" would land on whichever chat sends next.
    const real = activeId && activeId !== PAGE_AGENT_DRAFT ? activeId : undefined
    const saved = await uploadDocuments(list, uploadTarget)
    if (!saved.length) return
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

  /** A dictated clip goes in at the caret, read from the box as it is now (the clip took a while). Never sent. */
  const dictate = (raw: string): void => {
    const ta = box.current?.querySelector('textarea')
    const cur = ta?.value ?? text
    const at = Math.min(ta?.selectionStart ?? cur.length, cur.length)
    const ins = dictationText(raw, cur.slice(0, at))
    if (!ins) return
    setText(cur.slice(0, at) + ins + cur.slice(at))
    requestAnimationFrame(() => { ta?.focus(); ta?.setSelectionRange(at + ins.length, at + ins.length) })
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
    const client = onSend ? null : clientCommand(t)
    if (client) return runClient(client, k0)
    const entry = getDraft(k0)
    // The untrusted mark an upload left on a row-less draft is consumed by the next send; after a
    // relaunch only the draft remembers it, so it is re-armed here before the send reads it.
    if (entry?.taint && useStore.getState().uploadTaintTarget === null) {
      useStore.setState({ uploadTaintTarget: onSend ? 'page' : 'draft', uploadTaintSource: entry.taint })
    }
    recall.current = null
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
   * Once Stop is pressed the run is ending: a message then is a new turn (`send` waits for the stop to land),
   * not a follow-up parked in a queue the stop has just paused.
   */
  const submit = (mod = false): void => {
    if (!text.trim()) return
    // A built-in the UI handles ("/compact", "/skills") runs now, never queued: it is not a message for the reply.
    const action = queueId && !clientCommand(text) ? enterAction({ busy: streaming && !stopping, mod, cardPending, desk }) : 'send'
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
      {(suggested.length > 0 || presetHint.length > 0) && (
        <div className="skill-hints" aria-label="Skills that fit this message">
          {suggested.map((s) => (
            <button key={s.id} className="ghost-btn xs" title={`${s.description}\nPuts /skill ${skillSlug(s.name)} in front of your message.`} onClick={() => useSkill(s)}>
              <Sparkles size={11} /> Use {s.name}
            </button>
          ))}
          {presetHint.map((p) => (
            <button key={p.name} className="ghost-btn xs" disabled={importingPreset === p.name}
              title={`${p.blurb}\nA popular skill from ${p.source} (${p.license}). Imports as a candidate you approve under Library → Skills.`}
              onClick={() => void importPreset(p)}>
              <Download size={11} /> {importingPreset === p.name ? 'Importing…' : `Import the ${p.name} skill`}
            </button>
          ))}
        </div>
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
        <button className="icon-btn" title="Add files to this chat" aria-label="Add files to this chat" onClick={() => fileRef.current?.click()}><Paperclip size={16} /></button>
        <SmartTextarea
          kind="chat"
          variant="bare"
          rows={1}
          autoGrow
          maxHeight={240}
          minChars={8}
          value={text}
          onChange={(v) => { recall.current = null; setText(v) }}
          onPaste={onPaste}
          placeholder={streaming ? (queueId ? 'Queue a follow-up… ⌘↵ to steer now' : 'Steer the reply…') : placeholder}
          noGhost={!!slash}
          ariaLabel="Message"
          onKeyDown={(e) => {
            const act = slash && !e.shiftKey && !e.nativeEvent.isComposing ?slashMenuKey(e.key, slashActive, slash.length) : null
            if (act) {
              e.preventDefault()
              if (act.kind === 'move') setSlashActive(act.active)
              else if (act.kind === 'pick') setText(slash![slashActive].insert)
              else setSlashClosedAt(text)
            }
            else if (onRecallKey(e)) return
            else if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(e.metaKey || e.ctrlKey) }
            // Escape closes the decline prompt first, then ends the reply; while an input method is composing it belongs to the method.
            else if (e.key === 'Escape' && confirm && !e.nativeEvent.isComposing) { e.preventDefault(); setConfirm(null) }
            else if (e.key === 'Escape' && streaming && !e.nativeEvent.isComposing) { e.preventDefault(); halt() }
          }}
        />
        {slash && (
          <CaretMenu label="Commands" active={slashActive} onHover={setSlashActive}
            onPick={(i) => setText(slash[i].insert)}
            items={slash.map((c) => ({ key: c.key, label: c.label, hint: c.hint }))} />
        )}
        {/* Send keeps its slot for the whole reply (disabled until there is text to steer with), so
            typing mid-reply never changes the width of the text box; Stop sits beside it. */}
        <div className="composer-actions">
          <MicButton scope={box} onText={dictate} />
          {streaming && (
            <button className="send stop" title={stopping ? 'Stopping…' : 'Stop (Esc)'} aria-label={stopping ? 'Stopping' : 'Stop'} aria-busy={stopping} disabled={stopping} onClick={halt}>
              {stopping ? <Loader2 size={14} className="spin" /> : <Square size={14} />}
            </button>
          )}
          <button className="send" title={sendLabel} aria-label={sendLabel} disabled={!text.trim()} onClick={() => submit()}><ArrowUp size={16} /></button>
        </div>
      </div>
      {/* The plan-mode toggle binds ⌘⇧P itself, only for the focused conversation, so several mounted
          chat widgets do not all cycle at once. It comes last because its label grows with the mode, and
          nothing sits after it to be pushed. An empty page-agent panel has no chat for either toggle to set. */}
      <div className="composer-footer">
        {footer}
        {!onSend && (activeId
          ? chatPrivate && <span className="ghost-btn private-chat on" title="Nothing in this chat is remembered, learned from, or found by chat search"><EyeOff size={13} /> Private</span>
          : <button className={`ghost-btn private-chat ${chatPrivate ? 'on' : ''}`} aria-pressed={chatPrivate}
              title="Private: this chat reads no memories and teaches nothing, and chat search skips it. Fixed once the first message is sent."
              onClick={() => void setChatSettings({ private: !chatPrivate })}><EyeOff size={13} /> Private</button>)}
        {conversationId !== '\u0000page-agent' && (
          <>
            <SkipPermissionsToggle conversationId={conversationId} />
            <PlanModeToggle conversationId={conversationId} />
            <WorkingFolder conversationId={conversationId} />
          </>
        )}
      </div>
    </div>
  )
}
