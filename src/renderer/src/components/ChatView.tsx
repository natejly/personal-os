import { Fragment, useEffect, useRef, useState } from 'react'
import { PanelLeftOpen, Pencil, Sparkles, SlidersHorizontal, ArrowDown } from 'lucide-react'
import { useStore, useProject, useConversation, useIsStreaming, useStreamingMessageId, usePendingSends } from '../store'
import ProjectChip from './ProjectChip'
import MessageView, { PendingUserMessage } from './Message'
import RegenRow from './RegenRow'
import FindBar from './FindBar'
import Composer from './Composer'
import ChatControls from './ChatControls'
import ContextDrawer from './ContextDrawer'
import ResizeHandle from './ResizeHandle'
import PlanPanel from './PlanPanel'
import SendToSpace from './SendToSpace'
import { fenced, usePageContext } from '../lib/pageContext'
import AppSwitcher from './AppSwitcher'
import { useOnboarding } from './onboarding/onboardingStore'
import { firstPrompts } from './onboarding/steps'
import { useStickToBottom } from '../lib/stickToBottom'
import { dayKey, dayLabel } from '../lib/chatMeta'

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
  const isStreamingHere = useIsStreaming(conversationId)
  const streamingMessageId = useStreamingMessageId(conversationId)
  const pending = usePendingSends(conversationId)
  // A chat with no row yet: its first message is shown (with the dots) in place of the greeting.
  const draftPending = useStore((s) => (!conversationId && s.focusedConversationId === null ? s.draftPendingSend : null))
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const contextOpen = useStore((s) => s.contextOpen)
  const draftProjectId = useStore((s) => s.draftProjectId)
  const project = useProject(convo?.project_id ?? draftProjectId)
  const toggleSidebar = useStore((s) => s.toggleSidebar)
  const toggleContext = useStore((s) => s.toggleContext)
  const renameChat = useStore((s) => s.renameChat)
  const retitleChat = useStore((s) => s.retitleChat)
  const send = useStore((s) => s.send)
  const showFirstPrompts = useOnboarding((s) => s.firstPrompts && !conversationId)
  const google = useStore((s) => s.google)
  const setFirstPrompts = useOnboarding((s) => s.setFirstPrompts)
  // The chips are for the first empty chat only; once any conversation is open they are spent.
  useEffect(() => { if (conversationId) setFirstPrompts(false) }, [conversationId, setFirstPrompts])
  const scrollRef = useRef<HTMLDivElement>(null)
  const [editingTitle, setEditingTitle] = useState(false)

  const msgs = convo?.messages ?? []
  const lastLen = msgs[msgs.length - 1]?.content.length ?? 0

  const last = msgs[msgs.length - 1]
  const { stick, unseen, jump } = useStickToBottom(scrollRef, { resetKey: convo?.id ?? conversationId ?? null, tailUserId: last?.role === 'user' ? last.id : null, rows: msgs.length + pending.length + (draftPending ? 1 : 0) })

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
        {!sidebarOpen && <button className="icon-btn no-drag" title="Show sidebar (⌘B)" aria-label="Show sidebar" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
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
            <button className="title-btn" onClick={() => convo && setEditingTitle(true)} disabled={!convo}>
              {convo?.title ?? 'New chat'}
              {convo && <Pencil size={12} />}
            </button>
          )}
        </div>
        <div className="no-drag header-right">
          <SendToSpace items={[{ kind: 'chat', refId: convo?.id }]} disabled={!convo?.id} />
          <ProjectChip projectId={convo?.project_id ?? draftProjectId} />
          <button className={`icon-btn ${contextOpen ? 'on' : ''}`} title="Context panel (⌃⌘I)" aria-label="Toggle context panel" aria-pressed={contextOpen} onClick={toggleContext}><SlidersHorizontal size={16} /></button>
        </div>
        <AppSwitcher />
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
                    {firstPrompts(!!google?.connected).map((t) => <button key={t} className="ghost-btn" onClick={() => { setFirstPrompts(false); void send(t, conversationId) }}>{t}</button>)}
                  </div>
                )}
              </div>
            ) : (
              <div className="messages-inner">
                {msgs.map((m, i) => (
                  <Fragment key={m.id}>
                    {m.created_at > 0 && (i === 0 || dayKey(m.created_at) !== dayKey(msgs[i - 1].created_at)) && <div className="day-divider" role="separator">{dayLabel(m.created_at)}</div>}
                    <MessageView message={m} streaming={isStreamingHere && streamingMessageId === m.id} last={m.id === last?.id} editable={m.role === 'user' && !isStreamingHere} showContextChips />
                  </Fragment>
                ))}
                {pending.map((p) => <PendingUserMessage key={p.key} text={p.text} />)}
                {draftPending && <PendingUserMessage text={draftPending.text} />}
                {/* From the click, and from user_message to the first assistant row (context assembly), nothing else shows work. */}
                {(pending.length > 0 || draftPending || isStreamingHere) && streamingMessageId === null && (
                  <div className="msg assistant"><div className="avatar"><Sparkles size={14} /></div><div className="bubble"><span className="thinking"><span /><span /><span /></span></div></div>
                )}
                {pending.length === 0 && !draftPending && <RegenRow conversationId={conversationId} last={last} streaming={isStreamingHere} />}
              </div>
            )}
          </div>
          {(convo || draftPending) && !stick && (
            <button className="jump-latest" onClick={jump} aria-label="Jump to latest">
              <ArrowDown size={13} /> Jump to latest{unseen > 0 && <span className="jump-count">{unseen > 99 ? '99+' : unseen}</span>}
            </button>
          )}
          <PlanPanel conversationId={conversationId} />
          <Composer conversationId={conversationId} footer={<ChatControls conversationId={conversationId} />} />
        </div>
        {/* The drawer scrolls, so its handle sits on the chat body, pinned to the drawer's left edge. */}
        {contextOpen && <ResizeHandle id="context-drawer-w" defaultSize={340} min={260} max={640} grows="left" onCollapse={toggleContext} label="Context panel width" className="ctx-edge" />}
        {contextOpen && <ContextDrawer conversationId={conversationId} />}
      </div>
    </main>
  )
}
