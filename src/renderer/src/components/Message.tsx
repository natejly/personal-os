import { memo, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { remarkPlugins, rehypePlugins, normalizeMath } from '../lib/markdown'
import { Copy, Check, AlertCircle, User, Sparkles, Brain, Share2, FileText, Activity, ChevronRight, Lightbulb } from 'lucide-react'
import type { Message } from '@shared/types'
import { useStore } from '../store'
import ToolEvents from './ToolEvents'
import ChartBlock from './ChartBlock'
import MermaidBlock from './MermaidBlock'
import { traceSummary, fmtMs } from './TraceView'

function CopyButton({ text }: { text: string }): JSX.Element {
  const [ok, setOk] = useState(false)
  return (
    <button className="icon-btn ghost" title="Copy" onClick={() => { void navigator.clipboard.writeText(text); setOk(true); setTimeout(() => setOk(false), 1200) }}>
      {ok ? <Check size={13} /> : <Copy size={13} />}
    </button>
  )
}

function Pre({ streaming, ...props }: React.HTMLAttributes<HTMLPreElement> & { streaming?: boolean }): JSX.Element {
  const child = props.children as React.ReactElement<{ className?: string; children?: string }> | undefined
  const lang = child?.props?.className?.replace('hljs language-', '').replace('language-', '') ?? ''
  const code = String(child?.props?.children ?? '')
  // Blocks the model can use to render rich content instead of code (see RENDER_HINT in the backend).
  if (lang === 'chart') return <ChartBlock source={code} streaming={!!streaming} />
  if (lang === 'mermaid') return <MermaidBlock source={code} streaming={!!streaming} />
  return (
    <div className="code-block">
      <div className="code-head"><span>{lang || 'text'}</span><CopyButton text={code} /></div>
      <pre {...props} />
    </div>
  )
}

/** Chain-of-thought from a reasoning model. Open while it is the only thing happening, collapsed
 *  once the answer starts — reasoning models can think for 10s+ before the first content token. */
function Reasoning({ text, live }: { text: string; live: boolean }): JSX.Element {
  const [manual, setManual] = useState<boolean | null>(null)
  const open = manual ?? live
  const body = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (open && live && body.current) body.current.scrollTop = body.current.scrollHeight
  }, [text, open, live])
  return (
    <div className={`reasoning ${live ? 'live' : ''}`}>
      <button className="reasoning-head" onClick={() => setManual(!open)}>
        <ChevronRight size={12} className={open ? 'rot90' : ''} />
        <Lightbulb size={13} />
        <span className="reasoning-label">{live ? 'Thinking' : 'Thought process'}</span>
        {live && <span className="thinking mini"><span /><span /><span /></span>}
      </button>
      {open && <div className="reasoning-body" ref={body}>{text}</div>}
    </div>
  )
}

const MessageView = memo(function MessageView({ message, streaming }: { message: Message; streaming: boolean }): JSX.Element {
  const isUser = message.role === 'user'
  const { toggleContext, contextOpen, openTrace } = useStore()
  const ctx = message.context_used
  const ctxCount = ctx ? ctx.memories.length + ctx.nodes.length + ctx.chunks.length : 0
  const trace = message.trace && message.trace.length > 0 ? traceSummary(message.trace) : null
  return (
    <div className={`msg ${message.role}`}>
      <div className="avatar">{isUser ? <User size={14} /> : <Sparkles size={14} />}</div>
      <div className="bubble">
        {isUser ? (
          <div className="user-text markdown">
            <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins}>{normalizeMath(message.content)}</ReactMarkdown>
          </div>
        ) : (
          <div className="markdown">
            {message.reasoning && <Reasoning text={message.reasoning} live={streaming && !message.content} />}
            {message.tool_events && message.tool_events.length > 0 && <ToolEvents events={message.tool_events} />}
            {message.content ? (
              <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} components={{ pre: (p) => <Pre {...p} streaming={streaming} /> }}>{normalizeMath(message.content)}</ReactMarkdown>
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
              <button className="ctx-chip" title="Context used for this reply" onClick={() => !contextOpen && toggleContext()}>
                {ctx.memories.length > 0 && <span><Brain size={11} />{ctx.memories.length}</span>}
                {ctx.nodes.length > 0 && <span><Share2 size={11} />{ctx.nodes.length}</span>}
                {ctx.chunks.length > 0 && <span><FileText size={11} />{ctx.chunks.length}</span>}
              </button>
            )}
            {trace && (
              <button className="ctx-chip" title="Execution trace: LLM rounds, tool calls, timings and tokens" onClick={() => openTrace(message.id)}>
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
