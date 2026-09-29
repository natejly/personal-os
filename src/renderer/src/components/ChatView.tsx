import { useEffect, useRef, useState } from 'react'
import { PanelLeftOpen, ChevronDown, RefreshCw, Pencil, SlidersHorizontal, LayoutTemplate } from 'lucide-react'
import { useStore, useProject } from '../store'
import ProjectChip from './ProjectChip'
import MessageView from './Message'
import Composer from './Composer'
import ContextDrawer from './ContextDrawer'
import CanvasPanel from './CanvasPanel'

function ModelPicker({ value, onChange }: { value: string; onChange: (m: string) => void }): JSX.Element {
  const models = useStore((s) => s.models)
  const modelsError = useStore((s) => s.modelsError)
  const options = models.some((m) => m.id === value) ? models : [{ id: value }, ...models]
  return (
    <label className="model-picker" title={modelsError ?? 'Model (served via LiteLLM)'}>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
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

export default function ChatView(): JSX.Element {
  const convo = useStore((s) => s.active)
  const settings = useStore((s) => s.settings)
  const streaming = useStore((s) => s.streaming)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const contextOpen = useStore((s) => s.contextOpen)
  const draftProjectId = useStore((s) => s.draftProjectId)
  const project = useProject(convo?.project_id ?? draftProjectId)
  const { toggleSidebar, toggleContext, setChatModel, renameChat, regenerate, toggleCanvas } = useStore()
  const canvasOpen = useStore((s) => s.canvas.open)
  const canvasCount = useStore((s) => new Set([...s.artifacts.map((a) => a.identifier), ...Object.keys(s.artifactDrafts)]).size)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [stick, setStick] = useState(true)
  const [editingTitle, setEditingTitle] = useState(false)

  const model = convo?.model ?? settings.defaultModel
  const msgs = convo?.messages ?? []
  const lastLen = msgs[msgs.length - 1]?.content.length ?? 0

  useEffect(() => {
    if (stick) scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [lastLen, convo?.id, msgs.length, stick])

  const onScroll = (): void => {
    const el = scrollRef.current
    if (el) setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 80)
  }

  const isStreamingHere = !!streaming && streaming.conversationId === convo?.id
  const last = msgs[msgs.length - 1]

  return (
    <main className="chat">
      <header className="chat-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" title="Show sidebar (⌘B)" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <div className="chat-title no-drag">
          {convo && editingTitle ? (
            <input autoFocus defaultValue={convo.title}
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
          <ProjectChip projectId={convo?.project_id ?? draftProjectId} />
          <ModelPicker value={model} onChange={(m) => void setChatModel(m)} />
          {canvasCount > 0 && (
            <button className={`icon-btn ${canvasOpen ? 'on' : ''}`} title="Canvas (⌘⇧C)" onClick={toggleCanvas}><LayoutTemplate size={16} /></button>
          )}
          <button className={`icon-btn ${contextOpen ? 'on' : ''}`} title="Context panel (⌘I)" onClick={toggleContext}><SlidersHorizontal size={16} /></button>
        </div>
      </header>

      <div className="chat-body">
        <div className="chat-main">
          <div className="messages" ref={scrollRef} onScroll={onScroll}>
            {!convo ? (
              <div className="empty-state">
                <h1>{greeting()}</h1>
                <p>{project ? `New chat in ${project.name}. ` : ''}What are we working on?</p>
              </div>
            ) : (
              <div className="messages-inner">
                {msgs.map((m) => <MessageView key={m.id} message={m} streaming={isStreamingHere && streaming?.messageId === m.id} />)}
                {!isStreamingHere && last?.role === 'assistant' && (
                  <div className="regen-row">
                    <button className="ghost-btn" onClick={() => void regenerate()}><RefreshCw size={13} /> Regenerate</button>
                  </div>
                )}
              </div>
            )}
          </div>
          <Composer />
        </div>
        <CanvasPanel />
        {contextOpen && <ContextDrawer />}
      </div>
    </main>
  )
}
