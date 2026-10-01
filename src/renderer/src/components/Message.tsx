import { memo, useEffect, useRef, useState } from 'react'
import { AlertCircle, User, Sparkles, Brain, Share2, FileText, Activity, ChevronRight, Lightbulb } from 'lucide-react'
import type { Message } from '@shared/types'
import { useStore } from '../store'
import ToolEvents from './ToolEvents'
import MarkdownPreview, { CopyButton } from './MarkdownPreview'
export { SAFE_MD } from './MarkdownPreview'
import { traceSummary, fmtMs } from './TraceView'

/** Chain-of-thought from a reasoning model. Open while it is the only thing happening, collapsed once the answer starts. */
function Reasoning({ text, live }: { text: string; live: boolean }): JSX.Element {
  const [manual, setManual] = useState<boolean | null>(null)
  const open = manual ?? live
  const body = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (open && live && body.current) body.current.scrollTop = body.current.scrollHeight
  }, [text, open, live])
  return (
    <div className={`reasoning ${live ? 'live' : ''}`}>
      <button className="reasoning-head" onClick={() => setManual(!open)} aria-expanded={open}>
        <ChevronRight size={12} className={open ? 'rot90' : ''} />
        <Lightbulb size={13} />
        <span className="reasoning-label">{live ? 'Thinking' : 'Thought process'}</span>
        {live && <span className="thinking mini"><span /><span /><span /></span>}
      </button>
      {open && <div className="reasoning-body" ref={body}>{text}</div>}
    </div>
  )
}

// The store is read imperatively inside the handlers: any subscription here defeats the memo, and a
// streamed token would re-render every message in every mounted transcript.
const MessageView = memo(function MessageView({ message, streaming }: { message: Message; streaming: boolean }): JSX.Element {
  const isUser = message.role === 'user'
  const ctx = message.context_used
  const ctxCount = ctx ? ctx.memories.length + ctx.nodes.length + ctx.chunks.length : 0
  const trace = message.trace && message.trace.length > 0 ? traceSummary(message.trace) : null
  return (
    <div className={`msg ${message.role}`}>
      <div className="avatar">{isUser ? <User size={14} /> : <Sparkles size={14} />}</div>
      <div className="bubble">
        {isUser ? (
          <div className="user-bubble"><div className="user-text">{message.content}</div></div>
        ) : (
          <div className="markdown">
            {message.reasoning && <Reasoning text={message.reasoning} live={streaming && !message.content} />}
            {message.tool_events && message.tool_events.length > 0 && <ToolEvents events={message.tool_events} conversationId={message.conversation_id} />}
            {message.content ? (
              <MarkdownPreview source={message.content} streaming={streaming} />
            ) : streaming && !message.reasoning && !message.tool_events?.some((t) => t.pending) ? (
              <span className="thinking"><span /><span /><span /></span>
            ) : null}
            {streaming && message.content && <span className="cursor" />}
          </div>
        )}
        {message.error && <div className="msg-error"><AlertCircle size={14} /><span>{message.error}</span></div>}
        {!streaming && (
          <div className="msg-actions">
            {message.model && <span className="model-tag">{message.model}</span>}
            {ctx && ctxCount > 0 && (
              <button className="ctx-chip" title="Context used for this reply" onClick={() => { const s = useStore.getState(); if (!s.contextOpen) s.toggleContext() }}>
                {ctx.memories.length > 0 && <span><Brain size={11} />{ctx.memories.length}</span>}
                {ctx.nodes.length > 0 && <span><Share2 size={11} />{ctx.nodes.length}</span>}
                {ctx.chunks.length > 0 && <span><FileText size={11} />{ctx.chunks.length}</span>}
              </button>
            )}
            {trace && (
              <button className="ctx-chip" title="Execution trace: LLM rounds, tool calls, timings and tokens" onClick={() => useStore.getState().openTrace(message.id)}>
                <span><Activity size={11} />{trace.steps} step{trace.steps === 1 ? '' : 's'} · {fmtMs(trace.total_ms)}{trace.tokens ? ` · ${trace.tokens.toLocaleString()} tok` : ''}</span>
              </button>
            )}
            <CopyButton text={message.content} />
          </div>
        )}
      </div>
    </div>
  )
})

export default MessageView
