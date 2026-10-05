import { Component, memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import ChunkViewer, { type ChunkRef } from './ChunkViewer'
import SourcesList from './SourcesList'
import { citeInfo, openCite } from '../lib/remarkCites'
import { AlertCircle, User, Brain, Share2, FileText, Activity, ChevronRight, Lightbulb, Play, RotateCw, GraduationCap, Pencil, GitBranch, Trash2 } from 'lucide-react'
import type { Message, MessageStatus, RunChanges, ToolEvent } from '@shared/types'
import { useStore, useSubagents } from '../store'
import { api } from '../lib/api'
import ToolEvents, { agentIds } from './ToolEvents'
import MarkdownPreview, { CopyButton } from './MarkdownPreview'
export { SAFE_MD } from './MarkdownPreview'
import { traceSummary, fmtMs } from './TraceView'
import { modelLabel } from '../lib/modelLabel'
import { outcomeLabel } from '../lib/outcomeLabel'
import { describeCall, staysVisible } from '../lib/toolDisplay'
import { errorAction } from '../lib/errorAction'
import MessageEditor from './MessageEditor'
import { statusText, statusTicks, waitText } from '../lib/runStatus'
import { clockTime, fullTime } from '../lib/chatMeta'
import Face from './Face'

/**
 * One message's body, fenced: a render error in its markdown or tool cards (a null field, a bad
 * table) would otherwise unmount the whole app, since nothing above the chat catches it.
 */
class BodyBoundary extends Component<{ resetKey: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true }
  }
  componentDidUpdate(prev: { resetKey: string }): void {
    if (prev.resetKey !== this.props.resetKey && this.state.failed) this.setState({ failed: false })
  }
  render(): ReactNode {
    return this.state.failed ? <div className="msg-error"><AlertCircle size={14} /><span>Could not render this message.</span></div> : this.props.children
  }
}

/** A reply that called tools can be turned into a candidate skill. The id sent is this message, not the whole chat. */
function SaveSkill({ conversationId, messageId }: { conversationId: string; messageId: string }): JSX.Element {
  const [busy, setBusy] = useState(false)
  return (
    <button
      type="button"
      className="ctx-chip"
      disabled={busy}
      title="Turn this run into a skill for you to review. It is not used until you approve it."
      onClick={() => {
        setBusy(true)
        void useStore.getState().induceSkill(conversationId, messageId).finally(() => setBusy(false))
      }}
    >
      <GraduationCap size={11} /> {busy ? 'Saving…' : 'Save as skill'}
    </button>
  )
}

/**
 * One collapsed line per reply holding its chain-of-thought and every tool call that does not need the user.
 * Closed by default, streaming or not; only the user's click opens it, and that state lives here, so stream updates keep it.
 */
function ReplyActivity({ reasoning, events, conversationId, streaming, answering, browserSession }: { reasoning?: string | null; events: ToolEvent[]; conversationId: string; streaming: boolean; answering: boolean; browserSession?: string }): JSX.Element {
  const [open, setOpen] = useState(false)
  const body = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (open && streaming && body.current) body.current.scrollTop = body.current.scrollHeight
  }, [reasoning, open, streaming])
  const last = events[events.length - 1]
  // The subagents this reply started wear their faces on the line itself, so one click reaches a child's
  // transcript without opening the fold. Live state comes from the stream while the run is on.
  const subs = useSubagents(conversationId)
  const kids = useMemo(() => events.filter((t) => t.name === 'agent_spawn' && t.result_preview).flatMap((t) => agentIds(t.result_preview!)), [events])
  const label = [
    reasoning ? (streaming && !answering ? 'Thinking…' : 'Thought') : '',
    events.length ? `${events.length} tool call${events.length === 1 ? '' : 's'}` : '',
    streaming && !answering && last ? describeCall(last.name, last.arguments).verb : ''
  ].filter(Boolean).join(' · ')
  return (
    <div className={`reasoning ${streaming && !answering ? 'live' : ''}`}>
      <button className="reasoning-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <ChevronRight size={12} className={open ? 'rot90' : ''} />
        <Lightbulb size={13} />
        <span className="reasoning-label">{label}</span>
        {streaming && !answering && <span className="thinking mini"><span /><span /><span /></span>}
      </button>
      {kids.length > 0 && (
        <span className="reasoning-kids">
          {kids.map((id) => (
            <button key={id} className="crew-face" title={`${subs[id]?.role ?? 'subagent'}: ${subs[id]?.now || subs[id]?.state || 'open'}`} aria-label={`Open subagent ${id.slice(-4)}`}
              onClick={() => useStore.getState().openSubagent(id)}><Face name={id} status={subs[id]?.state} size={16} /></button>
          ))}
        </span>
      )}
      {open && reasoning && <div className="reasoning-body" ref={body}>{reasoning}</div>}
      {open && events.length > 0 && <div className="activity-tools"><ToolEvents events={events} conversationId={conversationId} streaming={streaming} browserSession={browserSession} /></div>}
    </div>
  )
}

/**
 * Continue / Resume under the newest reply when the backend says its run can be picked up. Bound to
 * its own message: a resumable run for some other message in the chat never shows here. The lookup
 * is repeated when the backend comes back, because it fails while the sidecar is down, and again when
 * the stream closes: at `done` the run row still reads `running` (its style-learning tail is on), and
 * only the close that follows `Run.end` says how it ended.
 */
function ContinueButton({ conversationId, messageId }: { conversationId: string; messageId: string }): JSX.Element | null {
  const backendState = useStore((s) => s.backendState)
  const settled = useStore((s) => !s.sessions[conversationId]?.streaming)
  const [run, setRun] = useState<{ id: string; reason: string } | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let live = true
    api.resumableRun(conversationId).then((r) => {
      if (live) setRun(r.resumable && r.run_id && r.message_id === messageId ? { id: r.run_id, reason: r.reason } : null)
    }).catch(() => undefined)
    return () => { live = false }
  }, [conversationId, messageId, backendState, settled])
  if (!run) return null
  return (
    <button className="ghost-btn" disabled={busy} onClick={() => {
      setBusy(true)
      useStore.getState().resumeRun(conversationId, run.id).then(() => setRun(null)).catch((e) => { setBusy(false); useStore.getState().toast((e as Error).message, 'error') })
    }}><Play size={13} /> {run.reason === 'interrupted' ? 'Resume' : 'Continue'}</button>
  )
}

/** The single action an error class earns, under the newest reply. */
function ErrorAction({ conversationId, kind }: { conversationId: string; kind: string }): JSX.Element | null {
  const [busy, setBusy] = useState(false)
  const act = errorAction(kind)
  if (!act) return null
  const st = (): ReturnType<typeof useStore.getState> => useStore.getState()
  const go = (): void => {
    if (act.action === 'retry') void st().regenerate(conversationId)
    else if (act.action === 'settings') st().openSettings('provider')
    else if (act.action === 'models') {
      // The model menu lives under the composer; settings is the fallback when no menu is mounted.
      const trigger = document.querySelector<HTMLButtonElement>('.model-menu-trigger')
      if (trigger) trigger.click()
      else st().openSettings('provider')
    } else {
      setBusy(true)
      api.compactConversation(conversationId)
        .then(() => st().regenerate(conversationId), (e) => st().toast(`Could not compact: ${(e as Error).message}`, 'error'))
        .catch((e) => st().toast((e as Error).message, 'error'))
        .finally(() => setBusy(false))
    }
  }
  return <button className="ghost-btn" disabled={busy} onClick={go}>{busy ? 'Working…' : act.label}</button>
}

// Tools that can change a granted folder; a reply without one never asks the backend for a change list.
const FILE_CHANGING = /^(write_local_file|move_local_file|trash_local_file|shell_run|opencode_run|fs_edit|fs_copy|fs_mkdir|desk_write_file|desk_trash_file|desk_import_sandbox)$/

/** "Files changed (n) · Undo" under a reply that changed files in a granted folder. Undo and Redo are the user's clicks. */
function FilesChanged({ messageId }: { messageId: string }): JSX.Element | null {
  const [ch, setCh] = useState<RunChanges | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  useEffect(() => {
    let live = true
    let timer: ReturnType<typeof setTimeout> | undefined
    // The after-snapshot lands just after the reply closes, so look again a couple of times before giving up.
    const look = (left: number): void => {
      api.messageChanges(messageId).then((r) => {
        if (!live) return
        if (r.count > 0 || left <= 0 || !r.available) setCh(r)
        else timer = setTimeout(() => look(left - 1), 1500)
      }).catch(() => undefined)
    }
    look(3)
    return () => { live = false; if (timer) clearTimeout(timer) }
  }, [messageId])
  if (!ch || ch.count === 0 || !ch.run_id) return null
  const undone = ch.state === 'undone'
  const go = (): void => {
    setBusy(true)
    const run = undone ? api.redoRun(ch.run_id as string) : api.undoRun(ch.run_id as string)
    run.then((r) => {
      setNote(r.edited_since.length ? `${r.edited_since.length} left alone (edited since): ${r.edited_since.slice(0, 3).join(', ')}` : '')
      setCh({ ...ch, state: undone ? 'applied' : 'undone' })
    }).catch((e) => useStore.getState().toast((e as Error).message, 'error')).finally(() => setBusy(false))
  }
  return (
    <div className="files-changed" title={ch.files.map((f) => `${f.status} ${f.path}`).slice(0, 30).join('\n')}>
      <FileText size={12} /> Files changed ({ch.count}) ·{' '}
      <button className="ghost-btn sm" disabled={busy} onClick={go}>{undone ? 'Redo' : 'Undo'}</button>
      {note && <span className="files-changed-note">{note}</span>}
    </div>
  )
}

/** What a silent stretch of a reply is waiting on. The 1s timer lives here, only while a countdown runs, so nothing above re-renders. */
function StatusLine({ status }: { status: MessageStatus }): JSX.Element {
  const [now, setNow] = useState(() => Date.now())
  const ticking = statusTicks(status, now)
  useEffect(() => {
    if (!ticking) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [ticking])
  return <div className="run-status" role="status">{statusText(status, now)}</div>
}

/** The three dots for a reply with nothing to show yet; past 5s they gain the elapsed time, so a slow model does not look hung. */
export function Thinking(): JSX.Element {
  const [start] = useState(() => Date.now())
  const [now, setNow] = useState(start)
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  const text = waitText(now - start)
  return <><span className="thinking"><span /><span /><span /></span>{text && <div className="run-status" role="status">{text}</div>}</>
}

/** The "N steps · ms · tok" chip, shown only with Settings → Behavior → Developer tools on. Its own component so the
 *  subscription stays out of MessageView. */
function TraceChip({ message }: { message: Message }): JSX.Element | null {
  const devTools = useStore((s) => s.settings.devTools === true)
  if (!devTools || !message.trace?.length) return null
  const trace = traceSummary(message.trace)
  return (
    <button className="ctx-chip" title="Execution trace: LLM rounds, tool calls, timings and tokens" onClick={() => useStore.getState().openTrace(message.id)}>
      <span><Activity size={11} />{trace.steps} step{trace.steps === 1 ? '' : 's'} · {fmtMs(trace.total_ms)}{trace.tokens ? ` · ${trace.tokens.toLocaleString()} tok` : ''}</span>
    </button>
  )
}

// The store is read imperatively inside the handlers: any subscription here defeats the memo, and a
// streamed token would re-render every message in every mounted transcript.
/** `showContextChips`: only ChatView mounts the context drawer, so only it shows chips that open it.
 *  `browserSession`: the agent browser this transcript drives, passed only to its latest reply that used the browser. */
/** The face a reply wears; a chat opened on an agent passes that agent's (see useChatFace), the default is the thread's own. */
export type ChatFace = { name: string; hue?: number }

const MessageView = memo(function MessageView({ message, streaming, last = false, editable = false, showContextChips = false, branchable = false, browserSession, face }: { message: Message; streaming: boolean; last?: boolean; editable?: boolean; showContextChips?: boolean; branchable?: boolean; browserSession?: string; face?: ChatFace }): JSX.Element {
  const [editing, setEditing] = useState(false)
  const isUser = message.role === 'user'
  const ctx = message.context_used
  const ctxCount = ctx ? ctx.memories.length + ctx.nodes.length + ctx.chunks.length : 0
  // Numbered sources this reply may cite as [n]; rows saved before numbering have no `n` and stay plain text.
  const chunks = ctx?.chunks
  const cites = useMemo(() => new Map((chunks ?? []).filter((c) => c.n).map((c) => [c.n!, citeInfo(c)])), [chunks])
  const [citing, setCiting] = useState<ChunkRef | null>(null)
  const onCite = useCallback((n: number) => { const c = chunks?.find((x) => x.n === n); if (c) openCite(c, setCiting) }, [chunks])
  // An interrupted row carries both an `Interrupted:` error and the outcome; the error line says it once.
  const note = !streaming && message.role === 'assistant' && !message.error ? outcomeLabel(message.outcome) : null
  const bare = !streaming && message.role === 'assistant' && message.outcome === 'stopped' && !message.content && !message.tool_events?.length && !message.reasoning
  // Calls that need the user (or that the user acts on) stay in place; the rest fold into the activity line.
  const events = message.tool_events
  const [shown, folded] = useMemo(() => [(events ?? []).filter(staysVisible), (events ?? []).filter((t) => !staysVisible(t))], [events])
  const summarized = !isUser && message.trace?.some((sp) => sp.kind === 'compact' && sp.meta?.kind === 'history')
  return (
    <div className={`msg ${message.role}`}>
      {/* The tinted, right-aligned bubble already says "you"; only the assistant gets a face, and each thread its own. */}
      {!isUser && <div className="avatar face-avatar"><Face name={face?.name ?? message.conversation_id} hue={face?.hue} status={streaming ? 'streaming' : message.error ? 'error' : undefined} /></div>}
      <div className="bubble">
        {isUser ? (
          editing ? (
            <MessageEditor message={message} onClose={() => setEditing(false)} />
          ) : (
            <div className="user-bubble"><div className="user-text">{message.content}</div></div>
          )
        ) : (
          <div className="msg-body">
            <BodyBoundary resetKey={message.id}>
              {(message.reasoning || folded.length > 0) && <ReplyActivity reasoning={message.reasoning} events={folded} conversationId={message.conversation_id} streaming={streaming} answering={!!message.content} browserSession={browserSession} />}
              {shown.length > 0 && <ToolEvents events={shown} conversationId={message.conversation_id} streaming={streaming} browserSession={browserSession} />}
              {/* Only the rendered text lives in .markdown: its element rules (p, ul, li) out-rank the
                  single-class rules the cards above are styled with. Its streaming class draws the cursor. */}
              {message.content ? (
                <div className={streaming ? 'markdown streaming' : 'markdown'}>
                  <MarkdownPreview source={message.content} streaming={streaming} cites={cites} onCite={onCite} />
                </div>
              ) : streaming && !message.reasoning && !folded.length && !shown.some((t) => t.pending) ? (
                <Thinking />
              ) : null}
            </BodyBoundary>
            {!streaming && chunks && <SourcesList content={message.content} chunks={chunks} onOpen={(c) => openCite(c, setCiting)} />}
            {citing && <ChunkViewer chunk={citing} onClose={() => setCiting(null)} />}
            {streaming && message.status && <StatusLine status={message.status} />}
          </div>
        )}
        {message.error && <div className="msg-error"><AlertCircle size={14} /><span>{message.error}</span></div>}
        {summarized && showContextChips && (
          <button className="compact-note" title="Open the context panel, where the summary lives" onClick={() => { const s = useStore.getState(); if (!s.contextOpen) s.toggleContext() }}>
            Earlier messages were summarized to fit the context window
          </button>
        )}
        {bare ? <div className="msg-partial">Stopped before any output</div> : note && <div className="msg-partial">{note}</div>}
        {last && !streaming && message.role === 'assistant' && message.error && message.error_kind && <div className="msg-error-actions"><ErrorAction conversationId={message.conversation_id} kind={message.error_kind} /></div>}
        {last && !streaming && message.role === 'assistant' && <ContinueButton conversationId={message.conversation_id} messageId={message.id} />}
        {!streaming && message.role === 'assistant' && message.tool_events?.some((t) => FILE_CHANGING.test(t.name)) && <FilesChanged messageId={message.id} />}
        {/* Always mounted and only hidden while the reply streams: the row's height is reserved, so
            nothing lands below the fold when the stream ends. */}
        {!editing && (
          <div className={streaming ? 'msg-actions streaming' : 'msg-actions'} aria-hidden={streaming || undefined}>
            {message.created_at > 0 && <time className="msg-time" dateTime={new Date(message.created_at * 1000).toISOString()} title={fullTime(message.created_at)}>{clockTime(message.created_at)}</time>}
            {message.model && (
              <span className="model-tag" title={message.model === modelLabel(message.model) ? undefined : message.model}>
                {modelLabel(message.model)}
              </span>
            )}
            {showContextChips && ctx && ctxCount > 0 && (
              <button className="ctx-chip" title="Context used for this reply" onClick={() => { const s = useStore.getState(); if (!s.contextOpen) s.toggleContext() }}>
                {ctx.memories.length > 0 && <span><Brain size={11} />{ctx.memories.length}</span>}
                {ctx.nodes.length > 0 && <span><Share2 size={11} />{ctx.nodes.length}</span>}
                {ctx.chunks.length > 0 && <span><FileText size={11} />{ctx.chunks.length}</span>}
              </button>
            )}
            {!isUser && (message.tool_events?.length ?? 0) > 0 && (
              <SaveSkill conversationId={message.conversation_id} messageId={message.id} />
            )}
            {showContextChips && <TraceChip message={message} />}
            {!bare && <CopyButton text={message.content} />}
            {editable && isUser && (
              <button type="button" className="ctx-chip" title="Edit and resend: this message and everything after it is hidden" aria-label="Edit message" onClick={() => setEditing(true)}>
                <Pencil size={11} />
              </button>
            )}
            {branchable && (
              <button type="button" className="ctx-chip" title="Branch in new chat: a copy of the conversation up to here; this one is left as it is"
                aria-label="Branch in new chat" onClick={() => void useStore.getState().forkChat(message.conversation_id, message.id)}>
                <GitBranch size={11} />
              </button>
            )}
            {editable && (
              <button type="button" className="ctx-chip" title="Delete this message (and its other regenerated versions)" aria-label="Delete message"
                onClick={() => { if (window.confirm('Delete this message? This cannot be undone.')) void useStore.getState().deleteMessage(message.conversation_id, message.id) }}>
                <Trash2 size={11} />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
})

/** A message sent and not yet confirmed by the run: the same bubble, dimmed, with no actions. */
export function PendingUserMessage({ text }: { text: string }): JSX.Element {
  return (
    <div className="msg user pending" aria-busy="true">
      <div className="avatar"><User size={14} /></div>
      <div className="bubble"><div className="user-bubble"><div className="user-text">{text}</div></div></div>
    </div>
  )
}

export default MessageView
