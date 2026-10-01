import { useEffect, useRef, useState } from 'react'
import { MessageSquarePlus, Sparkles, SquareArrowOutUpRight, X } from 'lucide-react'
import { useConversation, useIsStreaming, useStore, useStreamingMessageId } from '../store'
import Composer from './Composer'
import ResizeHandle from './ResizeHandle'
import MessageView from './Message'

/** `pick()` falls back to the focused chat on an undefined id, so an empty panel needs a dead key. */
const NO_THREAD = '\u0000page-agent'

/**
 * The page agent: ⌘I anywhere. It is an ordinary chat — same model, same tools, same history — that
 * carries a snapshot of the view behind it (see `usePageContext`), so "summarise this", "add these
 * as todos" or "move it to Thursday" resolve against what the user is actually looking at.
 */
export default function PageAgentPanel({ popout = false }: { popout?: boolean }): JSX.Element {
  const ctx = useStore((s) => s.pageContext)
  const threadId = useStore((s) => s.pageAgentId)
  const convo = useConversation(threadId ?? NO_THREAD)
  const streaming = useIsStreaming(threadId ?? NO_THREAD)
  const streamingMessageId = useStreamingMessageId(threadId ?? NO_THREAD)
  const sendToPageAgent = useStore((s) => s.sendToPageAgent)
  const closePageAgent = useStore((s) => s.closePageAgent)
  const resetPageAgent = useStore((s) => s.resetPageAgent)
  const selectChat = useStore((s) => s.selectChat)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [stick, setStick] = useState(true)

  const msgs = convo?.messages ?? []
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
    <aside className={`page-agent${popout ? ' popout' : ''}`} aria-label="Page agent">
      <ResizeHandle id="page-agent-w" defaultSize={380} min={280} max={720} grows="left" onCollapse={closePageAgent} label="Page agent width" className="at-left" />
      <header>
        <h3><Sparkles size={13} /> Ask about this page</h3>
        <div className="page-agent-actions">
          {convo && (
            <>
              <button className="icon-btn" title="New thread" aria-label="New thread" onClick={resetPageAgent}><MessageSquarePlus size={15} /></button>
              <button className="icon-btn" title="Open in Chats" aria-label="Open in Chats" onClick={() => void selectChat(convo.id)}><SquareArrowOutUpRight size={15} /></button>
            </>
          )}
          <button className="icon-btn" title="Close (⌘I)" aria-label="Close page agent" onClick={closePageAgent}><X size={15} /></button>
        </div>
      </header>

      <div className="page-agent-ctx" title={ctx?.detail ? `${ctx.detail.slice(0, 600)}…` : undefined}>
        {ctx ? <><b>{ctx.label}</b>{ctx.selection ? <em> · selection</em> : null}</> : <span className="muted">This screen has no context to send.</span>}
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
          msgs.map((m) => <MessageView key={m.id} message={m} streaming={streaming && streamingMessageId === m.id} />)
        )}
      </div>

      <Composer
        conversationId={threadId ?? NO_THREAD}
        compact
        onSend={sendToPageAgent}
        placeholder={ctx ? `Ask about ${ctx.label}…` : 'Ask…'}
      />
    </aside>
  )
}
