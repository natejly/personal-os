import { useEffect, useRef, useState } from 'react'
import { PanelLeftOpen, ChevronDown, RefreshCw, Pencil, SlidersHorizontal } from 'lucide-react'
import type { Effort } from '@shared/types'
import { useStore, useProject, useConversation, useIsStreaming, useStreamingMessageId } from '../store'
import ProjectChip from './ProjectChip'
import MessageView from './Message'
import Composer from './Composer'
import ContextDrawer from './ContextDrawer'
import ResizeHandle from './ResizeHandle'
import PlanPanel from './PlanPanel'
import SendToSpace from './SendToSpace'
import { clip, usePageContext } from '../lib/pageContext'

function ModelPicker({ value, onChange }: { value: string; onChange: (m: string) => void }): JSX.Element {
  const models = useStore((s) => s.models)
  const modelsError = useStore((s) => s.modelsError)
  const options = models.some((m) => m.id === value) ? models : [{ id: value }, ...models]
  return (
    <label className="model-picker" title={modelsError ?? 'Model (served via LiteLLM)'}>
      <select aria-label="Model" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
      </select>
      <ChevronDown size={14} />
    </label>
  )
}

const EFFORTS: Effort[] = ['default', 'low', 'medium', 'high']

/**
 * Reasoning effort, next to the model it belongs with. A chat window has always had this control; the
 * page did not, so the only way to raise effort was to open the chat as a widget. `setChatSettings`
 * parks the choice on `draftEffort` while the chat is still a draft.
 */
function EffortPicker({ value, onChange }: { value: Effort; onChange: (e: Effort) => void }): JSX.Element {
  return (
    <label className="model-picker effort-picker" title="Reasoning effort">
      <select aria-label="Reasoning effort" value={value} onChange={(e) => onChange(e.target.value as Effort)}>
        {EFFORTS.map((x) => <option key={x} value={x}>{x === 'default' ? 'Effort: default' : `Effort: ${x}`}</option>)}
      </select>
      <ChevronDown size={14} />
    </label>
  )
}

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
  const settings = useStore((s) => s.settings)
  const isStreamingHere = useIsStreaming(conversationId)
  const streamingMessageId = useStreamingMessageId(conversationId)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const contextOpen = useStore((s) => s.contextOpen)
  const draftProjectId = useStore((s) => s.draftProjectId)
  const draftEffort = useStore((s) => s.draftEffort)
  const project = useProject(convo?.project_id ?? draftProjectId)
  const { toggleSidebar, toggleContext, setChatModel, setChatSettings, renameChat, regenerate } = useStore()
  const scrollRef = useRef<HTMLDivElement>(null)
  const [stick, setStick] = useState(true)
  const [editingTitle, setEditingTitle] = useState(false)

  const model = convo?.model ?? settings.defaultModel
  const effort = convo?.settings?.effort ?? draftEffort
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
      ? `The user is reading this conversation (\`${convo.id}\`). Its last turns:\n\n${clip(msgs.slice(-6).map((m) => `**${m.role}**: ${m.content}`).join('\n\n'), 3000)}`
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
          <ModelPicker value={model} onChange={(m) => void setChatModel(m, conversationId)} />
          <EffortPicker value={effort} onChange={(e) => void setChatSettings({ effort: e }, conversationId)} />
          <button className={`icon-btn ${contextOpen ? 'on' : ''}`} title="Context panel (⌃⌘I)" aria-label="Toggle context panel" aria-pressed={contextOpen} onClick={toggleContext}><SlidersHorizontal size={16} /></button>
        </div>
      </header>

      <div className="chat-body">
        <div className="chat-main">
          <div className="messages" ref={scrollRef} onScroll={onScroll}>
            {!convo ? (
              <div className="empty-state">
                <h1>{greeting()}</h1>
                {project && <p>New chat in {project.name}</p>}
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
          <Composer conversationId={conversationId} />
        </div>
        {/* The drawer scrolls, so its handle sits on the chat body, pinned to the drawer's left edge. */}
        {contextOpen && <ResizeHandle id="context-drawer-w" defaultSize={340} min={260} max={640} grows="left" onCollapse={toggleContext} label="Context panel width" className="ctx-edge" />}
        {contextOpen && <ContextDrawer conversationId={conversationId} />}
      </div>
    </main>
  )
}
