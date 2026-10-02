import { useEffect, useRef, useState } from 'react'
import { PanelLeftOpen, RefreshCw, Pencil, SlidersHorizontal } from 'lucide-react'
import { useStore, useProject, useConversation, useIsStreaming, useStreamingMessageId } from '../store'
import ProjectChip from './ProjectChip'
import MessageView from './Message'
import Composer from './Composer'
import ChatControls from './ChatControls'
import ContextDrawer from './ContextDrawer'
import ResizeHandle from './ResizeHandle'
import PlanPanel from './PlanPanel'
import SendToSpace from './SendToSpace'
import { fenced, usePageContext } from '../lib/pageContext'
import AppSwitcher from './AppSwitcher'
import { useOnboarding } from './onboarding/onboardingStore'
import { FIRST_PROMPTS } from './onboarding/steps'

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
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const contextOpen = useStore((s) => s.contextOpen)
  const draftProjectId = useStore((s) => s.draftProjectId)
  const project = useProject(convo?.project_id ?? draftProjectId)
  const { toggleSidebar, toggleContext, renameChat, regenerate, send } = useStore()
  const firstPrompts = useOnboarding((s) => s.firstPrompts && !conversationId)
  const setFirstPrompts = useOnboarding((s) => s.setFirstPrompts)
  // The chips are for the first empty chat only; once any conversation is open they are spent.
  useEffect(() => { if (conversationId) setFirstPrompts(false) }, [conversationId, setFirstPrompts])
  const scrollRef = useRef<HTMLDivElement>(null)
  const [stick, setStick] = useState(true)
  const [editingTitle, setEditingTitle] = useState(false)

  const msgs = convo?.messages ?? []
  const lastLen = msgs[msgs.length - 1]?.content.length ?? 0

  useEffect(() => {
    if (stick) scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [lastLen, convo?.id, msgs.length, stick])

  const onScroll = (): void => {
    const el = scrollRef.current
    if (el) setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 80)
  }

  const last = msgs[msgs.length - 1]

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
            <input autoFocus aria-label="Chat title" defaultValue={convo.title}
              onBlur={(e) => { void renameChat(convo.id, e.target.value); setEditingTitle(false) }}
              onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setEditingTitle(false) }} />
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
          <div className="messages" ref={scrollRef} onScroll={onScroll}>
            {!convo ? (
              <div className="empty-state">
                <h1>{greeting()}</h1>
                {project && <p>New chat in {project.name}</p>}
                {firstPrompts && (
                  <div className="ob-first-prompts" role="group" aria-label="Things to try">
                    {FIRST_PROMPTS.map((t) => <button key={t} className="ghost-btn" onClick={() => { setFirstPrompts(false); void send(t, conversationId) }}>{t}</button>)}
                  </div>
                )}
              </div>
            ) : (
              <div className="messages-inner">
                {msgs.map((m) => <MessageView key={m.id} message={m} streaming={isStreamingHere && streamingMessageId === m.id} />)}
                {!isStreamingHere && last?.role === 'assistant' && (
                  <div className="regen-row">
                    <button className="ghost-btn" onClick={() => void regenerate(conversationId)}><RefreshCw size={13} /> Regenerate</button>
                  </div>
                )}
              </div>
            )}
          </div>
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
