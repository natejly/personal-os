import { Fragment, useEffect, useRef, useState } from 'react'
import { isClear } from '../lib/workers'
import { Pencil, Sparkles, SlidersHorizontal, ArrowDown, PanelRight } from 'lucide-react'
import { useStore, useProject, useChatFace, useConversation, useIsStreaming, useStreamingMessageId, usePendingSends } from '../store'
import MessageView, { PendingUserMessage, Thinking } from './Message'
import RegenRow from './RegenRow'
import FindBar from './FindBar'
import Composer from './Composer'
import ChatControls from './ChatControls'
import ContextDrawer from './ContextDrawer'
import ResizeHandle from './ResizeHandle'
import WorkersPanel from './WorkersPanel'
import ShowPanel from './ShowPanel'
import SendToSpace from './SendToSpace'
import ChatFilesButton from './ChatFilesPanel'
import { fenced, usePageContext } from '../lib/pageContext'
import AppSwitcher from './AppSwitcher'
import SidebarToggle from './SidebarToggle'
import { useOnboarding } from './onboarding/onboardingStore'
import { firstPrompts } from './onboarding/steps'
import { pimConnected } from '../lib/pim'
import { useStickToBottom } from '../lib/stickToBottom'
import { dayKey, dayLabel } from '../lib/chatMeta'
import { chatBrowserSession, deskBrowserSession, latestBrowserMessage } from '../lib/browserApproval'
import { DeskInline } from './DeskStrip'
import DeskPanel from './DeskPanel'
import Face from './Face'
import TelegramIcon from './TelegramIcon'
import { chatLabel, isTelegramChat } from '../lib/chatRows'

function greeting(): string {
  const h = new Date().getHours()
  if (h < 5) return 'Burning the midnight oil?'
  if (h < 12) return 'Good morning.'
  if (h < 18) return 'Good afternoon.'
  return 'Good evening.'
}

/** `conversationId` is omitted in classic mode, where the focused session is the only one on screen. */
export default function ChatView({ conversationId }: { conversationId?: string }): JSX.Element {
  const convo = useConversation(conversationId)
  // The backend refuses an edit of, and a branch from, a desk or job transcript.
  const isDeskOrJob = !!convo?.settings.deskId || !!convo?.settings.job_id
  const face = useChatFace(convo)
  const isStreamingHere = useIsStreaming(conversationId)
  const streamingMessageId = useStreamingMessageId(conversationId)
  const pending = usePendingSends(conversationId)
  // A chat with no row yet: its first message is shown (with the dots) in place of the greeting.
  const draftPending = useStore((s) => (!conversationId && s.focusedConversationId === null ? s.draftPendingSend : null))
  const contextOpen = useStore((s) => s.contextOpen)
  const draftProjectId = useStore((s) => s.draftProjectId)
  const project = useProject(convo?.project_id ?? draftProjectId)
  const toggleContext = useStore((s) => s.toggleContext)
  const renameChat = useStore((s) => s.renameChat)
  const retitleChat = useStore((s) => s.retitleChat)
  const send = useStore((s) => s.send)
  const showFirstPrompts = useOnboarding((s) => s.firstPrompts && !conversationId)
  const pimOn = useStore(pimConnected)
  const setFirstPrompts = useOnboarding((s) => s.setFirstPrompts)
  // The chips are for the first empty chat only; once any conversation is open they are spent.
  useEffect(() => { if (conversationId) setFirstPrompts(false) }, [conversationId, setFirstPrompts])
  const scrollRef = useRef<HTMLDivElement>(null)
  const [editingTitle, setEditingTitle] = useState(false)
  // The side panel (the `show` tool, or "Open in panel" on a block) belongs to this chat alone.
  const showKey = convo?.id ?? conversationId ?? ''
  const showing = useStore((s) => (showKey ? !!s.shows[showKey] : false))

  // A chat working autonomously: the full-window chat opens its desk (plan, cards, outputs) and owns the panel.
  const deskId = convo?.settings.deskId || undefined
  const desk = useStore((s) => (!conversationId && deskId && s.activeDesk?.id === deskId ? s.activeDesk : null))
  const openDesk = useStore((s) => s.openDesk)
  const markDeskSeen = useStore((s) => s.markDeskSeen)
  const [deskPanel, setDeskPanel] = useState(false)
  useEffect(() => { if (deskId && !conversationId) void openDesk(deskId) }, [deskId, conversationId, openDesk])
  // Looking at the chat is the acknowledgement. Keyed on desk+count so a failed POST /seen is not retried every render.
  const deskUnseen = desk?.unseen ?? 0
  const seenTried = useRef('')
  useEffect(() => {
    const key = `${desk?.id}:${deskUnseen}`
    if (!desk || deskUnseen === 0 || seenTried.current === key) return
    seenTried.current = key
    void markDeskSeen(desk.id)
  }, [desk, deskUnseen, markDeskSeen])

  const msgs = convo?.messages ?? []
  const lastLen = msgs[msgs.length - 1]?.content.length ?? 0

  const last = msgs[msgs.length - 1]
  const watchId = latestBrowserMessage(msgs)
  const { stick, unseen, jump, release } = useStickToBottom(scrollRef, { resetKey: convo?.id ?? conversationId ?? null, tailUserId: last?.role === 'user' ? last.id : null, rows: msgs.length + pending.length + (draftPending ? 1 : 0) })

  // "Open in chat" from a memory's source: scroll to that message once the transcript is in. The history is
  // not windowed, so a message missing from a loaded chat is gone. The frame wait lets the stick-to-bottom
  // pass for a fresh chat run first; release() then keeps it from pulling the view back down.
  const chatJump = useStore((s) => s.chatJump)
  useEffect(() => {
    if (conversationId || !chatJump || chatJump.conversationId !== convo?.id || !msgs.length) return
    const raf = requestAnimationFrame(() => {
      const el = scrollRef.current?.querySelector<HTMLElement>(`[data-message-id="${CSS.escape(chatJump.messageId)}"]`)
      useStore.setState({ chatJump: null })
      if (!el) return useStore.getState().toast('That message is no longer in this chat', 'info')
      release()
      el.scrollIntoView({ block: 'center' })
      el.classList.add('msg-flash')
      setTimeout(() => el.classList.remove('msg-flash'), 2000)
    })
    return () => cancelAnimationFrame(raf)
  }, [chatJump, msgs, conversationId, convo?.id, release])

  // Only the full-window chat is a "page"; a chat window on the canvas is one of many on screen.
  usePageContext(() => (conversationId ? undefined : {
    view: 'chat',
    label: convo ? `Chat “${convo.title}”` : 'Chat',
    detail: convo
      ? `The user is reading this conversation (\`${convo.id}\`). Its last turns:\n\n${fenced(msgs.slice(-6).map((m) => `**${m.role}**: ${m.content}`).join('\n\n'), 3000)}`
      : 'An empty chat, nothing sent yet.',
    refs: convo ? [{ kind: 'conversation', id: convo.id, name: convo.title }] : [],
    hints: convo ? ['Summarise this conversation', 'What did we decide?'] : []
  }), [conversationId, convo?.id, convo?.title, msgs.length, lastLen])

  return (
    <main className="chat">
      <header className="chat-header drag">
        <SidebarToggle />
        <div className="chat-title no-drag">
          {convo && editingTitle ? (
            <>
              <input autoFocus aria-label="Chat title" defaultValue={convo.title}
                onBlur={(e) => { void renameChat(convo.id, e.target.value); setEditingTitle(false) }}
                onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setEditingTitle(false) }} />
              {/* mousedown is stopped so the input does not blur (and rename) before the click lands */}
              <button className="icon-btn" title="Suggest a title" aria-label="Suggest a title"
                onMouseDown={(e) => e.preventDefault()} onClick={() => { setEditingTitle(false); void retitleChat(convo.id) }}><Sparkles size={14} /></button>
            </>
          ) : (
            <>
              {convo && isTelegramChat(convo) && <span style={{ display: 'flex', alignItems: 'center', paddingLeft: 8, color: 'var(--text-muted)' }}><TelegramIcon size={14} /></span>}
              <button className="title-btn" onClick={() => convo && setEditingTitle(true)} disabled={!convo}>
                {convo ? chatLabel(convo) : 'New chat'}
                {convo && <Pencil size={12} />}
              </button>
            </>
          )}
        </div>
        <div className="no-drag header-right">
          {conversationId && <ChatFilesButton conversationId={convo?.id} />}
          <SendToSpace items={[{ kind: 'chat', refId: convo?.id }]} disabled={!convo?.id} />
          <button className={`icon-btn ${contextOpen ? 'on' : ''}`} title="Context panel (⌃⌘I)" aria-label="Toggle context panel" aria-pressed={contextOpen} onClick={toggleContext}><SlidersHorizontal size={16} /></button>
        </div>
        {/* The full-window chat gives the last title-bar slot to its own workspace panel; ⌘I still opens the quick chat. */}
        {conversationId ? <AppSwitcher /> : (
          <div className="app-switcher no-drag">
            <button className={`icon-btn${deskPanel ? ' on' : ''}`} title="Workspace panel" aria-label="Workspace panel" aria-pressed={deskPanel} disabled={!convo?.id}
              onClick={() => setDeskPanel((o) => !o)}><PanelRight size={15} /></button>
          </div>
        )}
      </header>

      <div className="chat-body">
        <div className="chat-main">
          {/* Highlight names are document-global, so only the full-window chat owns find. */}
          {!conversationId && <FindBar scope={scrollRef} resetKey={convo?.id} />}
          <div className="messages" ref={scrollRef}>
            {!convo && !draftPending ? (
              <div className="empty-state">
                <h1>{greeting()}</h1>
                {project && <p>New chat in {project.name}</p>}
                {showFirstPrompts && (
                  <div className="ob-first-prompts" role="group" aria-label="Things to try">
                    {firstPrompts(pimOn).map((t) => <button key={t} className="ghost-btn" onClick={() => { setFirstPrompts(false); void send(t, conversationId) }}>{t}</button>)}
                  </div>
                )}
              </div>
            ) : (
              <div className="messages-inner">
                {msgs.map((m, i) => (
                  <Fragment key={m.id}>
                    {m.created_at > 0 && (i === 0 || dayKey(m.created_at) !== dayKey(msgs[i - 1].created_at)) && <div className="day-divider" role="separator">{dayLabel(m.created_at)}</div>}
                    {isClear(m) ? <div className="day-divider" role="separator">Context cleared</div> : <MessageView message={m} face={face} streaming={isStreamingHere && streamingMessageId === m.id} last={m.id === last?.id} editable={!isStreamingHere} resendable={!isStreamingHere && !isDeskOrJob} showContextChips
                      branchable={m.created_at > 0 && !isDeskOrJob}
                      browserSession={m.id === watchId ? (deskId ? deskBrowserSession(deskId) : chatBrowserSession(m.conversation_id)) : undefined} />}
                  </Fragment>
                ))}
                {pending.map((p) => <PendingUserMessage key={p.key} text={p.text} attachments={p.attachments} />)}
                {draftPending && <PendingUserMessage text={draftPending.text} attachments={draftPending.attachments} />}
                {/* From the click, and from user_message to the first assistant row (context assembly), nothing else shows work.
                    A brand-new chat has no id yet, so its slot stays empty rather than showing a face that would change once the row lands. */}
                {(pending.length > 0 || draftPending || isStreamingHere) && streamingMessageId === null && (
                  <div className="msg assistant"><div className="avatar face-avatar">{(convo?.id ?? conversationId) && <Face {...face} name={face.name || conversationId!} status="streaming" />}</div><div className="bubble"><Thinking /></div></div>
                )}
                {desk && <DeskInline desk={desk} events={msgs.flatMap((m) => m.tool_events ?? [])} />}
                {pending.length === 0 && !draftPending && <RegenRow conversationId={convo?.id ?? conversationId} last={last} streaming={isStreamingHere} />}
              </div>
            )}
          </div>
          {(convo || draftPending) && !stick && (
            <button className="jump-latest" onClick={jump} aria-label="Jump to latest">
              <ArrowDown size={13} /> Jump to latest{unseen > 0 && <span className="jump-count">{unseen > 99 ? '99+' : unseen}</span>}
            </button>
          )}
          <WorkersPanel conversationId={conversationId} />
          <Composer conversationId={conversationId} footer={<ChatControls conversationId={conversationId} />} />
        </div>
        {showing && <ShowPanel conversationId={showKey} />}
        {convo?.id && !conversationId && deskPanel && <DeskPanel key={convo.id} desk={desk} conversationId={convo.id} onClose={() => setDeskPanel(false)} />}
        {/* The drawer scrolls, so its handle sits on the chat body, pinned to the drawer's left edge. */}
        {contextOpen && <ResizeHandle id="context-drawer-w" defaultSize={340} min={260} max={640} grows="left" onCollapse={toggleContext} label="Context panel width" className="ctx-edge" />}
        {contextOpen && <ContextDrawer conversationId={conversationId} />}
      </div>
    </main>
  )
}
