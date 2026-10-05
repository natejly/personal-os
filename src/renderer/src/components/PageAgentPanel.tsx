import { useEffect, useRef, useState } from 'react'
import { MessageSquarePlus, Pin, PinOff, SquareArrowOutUpRight, X } from 'lucide-react'
import { PAGE_AGENT_DRAFT, useConversation, useIsStreaming, useStore, useStreamingMessageId } from '../store'
import { panelConversationFor } from '../lib/pagePanel'
import ChatControls from './ChatControls'
import Composer from './Composer'
import ResizeHandle from './ResizeHandle'
import MessageView from './Message'
import { chatBrowserSession, latestBrowserMessage } from '../lib/browserApproval'

/**
 * The page agent: ⌘I anywhere. It is an ordinary chat — same model, same tools, same history — that
 * carries a snapshot of the view behind it (see `usePageContext`), so "summarise this", "add these
 * as todos" or "move it to Thursday" resolve against what the user is actually looking at.
 */
export default function PageAgentPanel(): JSX.Element {
  const pin = useStore((s) => s.pageAgentPin)
  const liveCtx = useStore((s) => s.pageContext)
  // Pinned, the panel keeps describing the view it was pinned on, not whatever is on screen now.
  const ctx = pin ? pin.ctx : liveCtx
  const threadId = useStore((s) => panelConversationFor({ pageAgentId: s.pageAgentId, pin: s.pageAgentPin }))
  const pinPageAgent = useStore((s) => s.pinPageAgent)
  const unpinPageAgent = useStore((s) => s.unpinPageAgent)
  const convo = useConversation(threadId ?? PAGE_AGENT_DRAFT)
  const streaming = useIsStreaming(threadId ?? PAGE_AGENT_DRAFT)
  const streamingMessageId = useStreamingMessageId(threadId ?? PAGE_AGENT_DRAFT)
  const sendToPageAgent = useStore((s) => s.sendToPageAgent)
  const closePageAgent = useStore((s) => s.closePageAgent)
  const resetPageAgent = useStore((s) => s.resetPageAgent)
  const selectChat = useStore((s) => s.selectChat)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [stick, setStick] = useState(true)

  const msgs = convo?.messages ?? []
  const watchId = latestBrowserMessage(msgs)
  const lastLen = msgs[msgs.length - 1]?.content.length ?? 0
  useEffect(() => {
    if (stick) scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [lastLen, msgs.length, convo?.id, stick])

  const onScroll = (): void => {
    const el = scrollRef.current
    if (el) setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 80)
  }

  const hints = ctx?.hints ?? []

  return (
    <aside className="page-agent" aria-label="Page agent">
      <ResizeHandle id="page-agent-w" defaultSize={380} min={280} max={720} grows="left" onCollapse={closePageAgent} label="Page agent width" className="at-left" />
      <div className="page-agent-ctx" title={ctx?.detail ? `${ctx.detail.slice(0, 600)}…` : undefined}>
        <span className="page-agent-label">
          {pin ? <><Pin size={11} /> <b>{pin.label}</b></> : ctx ? <><b>{ctx.label}</b>{ctx.selection ? <em> · selection</em> : null}</> : <span className="muted">This screen has no context to send.</span>}
        </span>
        <div className="page-agent-actions">
          {pin ? (
            <button className="ghost-btn xs" title="Unpin and return to this page" onClick={unpinPageAgent}><PinOff size={12} /> Back to this page</button>
          ) : (
            <button className="icon-btn" title="Pin to this page: the panel keeps this chat when you switch views" aria-label="Pin to this page" onClick={pinPageAgent}><Pin size={15} /></button>
          )}
          {convo && (
            <>
              <button className="icon-btn" title="New thread" aria-label="New thread" onClick={resetPageAgent}><MessageSquarePlus size={15} /></button>
              <button className="icon-btn" title="Open in Chats" aria-label="Open in Chats" onClick={() => void selectChat(convo.id)}><SquareArrowOutUpRight size={15} /></button>
            </>
          )}
          <button className="icon-btn" title="Close (⌘I)" aria-label="Close page agent" onClick={closePageAgent}><X size={15} /></button>
        </div>
      </div>

      <div className="page-agent-body" ref={scrollRef} onScroll={onScroll}>
        {msgs.length === 0 ? (
          <div className="page-agent-empty">
            <p className="muted">Whatever you ask goes out with what is on screen behind this panel.</p>
            {hints.map((h) => (
              <button key={h} className="page-agent-hint" onClick={() => void sendToPageAgent(h)}>{h}</button>
            ))}
          </div>
        ) : (
          msgs.map((m) => <MessageView key={m.id} message={m} streaming={streaming && streamingMessageId === m.id}
            browserSession={m.id === watchId ? chatBrowserSession(m.conversation_id) : undefined} />)
        )}
      </div>

      <Composer
        conversationId={threadId ?? PAGE_AGENT_DRAFT}
        draftKey="page"
        compact
        footer={<ChatControls conversationId={threadId ?? PAGE_AGENT_DRAFT} />}
        onSend={sendToPageAgent}
        placeholder={ctx ? `Ask about ${ctx.label}…` : 'Ask…'}
      />
    </aside>
  )
}
