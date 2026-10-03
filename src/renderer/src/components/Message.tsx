import { Component, memo, useEffect, useRef, useState, type ReactNode } from 'react'
import { AlertCircle, User, Sparkles, Brain, Share2, FileText, Activity, ChevronRight, Lightbulb, RotateCw, GraduationCap, Pencil } from 'lucide-react'
import type { Message, RunChanges } from '@shared/types'
import { useStore } from '../store'
import { api } from '../lib/api'
import ToolEvents from './ToolEvents'
import MarkdownPreview, { CopyButton } from './MarkdownPreview'
export { SAFE_MD } from './MarkdownPreview'
import { traceSummary, fmtMs } from './TraceView'
import { modelLabel } from '../lib/modelLabel'
import { outcomeLabel } from '../lib/outcomeLabel'
import { errorAction } from '../lib/errorAction'
import MessageEditor from './MessageEditor'

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
    }}><RotateCw size={13} /> {run.reason === 'interrupted' ? 'Resume' : 'Continue'}</button>
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
const FILE_CHANGING = /^(write_local_file|move_local_file|trash_local_file|shell_run|fs_edit|fs_copy|fs_mkdir|desk_write_file|desk_trash_file|desk_import_sandbox)$/

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
      <button className="ghost-btn" disabled={busy} onClick={go}>{undone ? 'Redo' : 'Undo'}</button>
      {note && <span className="files-changed-note">{note}</span>}
    </div>
  )
}

// The store is read imperatively inside the handlers: any subscription here defeats the memo, and a
// streamed token would re-render every message in every mounted transcript.
const MessageView = memo(function MessageView({ message, streaming, last = false, editable = false }: { message: Message; streaming: boolean; last?: boolean; editable?: boolean }): JSX.Element {
  const [editing, setEditing] = useState(false)
  const isUser = message.role === 'user'
  const ctx = message.context_used
  const ctxCount = ctx ? ctx.memories.length + ctx.nodes.length + ctx.chunks.length : 0
  // An interrupted row carries both an `Interrupted:` error and the outcome; the error line says it once.
  const note = !streaming && message.role === 'assistant' && !message.error ? outcomeLabel(message.outcome) : null
  const bare = !streaming && message.role === 'assistant' && message.outcome === 'stopped' && !message.content && !message.tool_events?.length && !message.reasoning
  const trace = message.trace && message.trace.length > 0 ? traceSummary(message.trace) : null
  return (
    <div className={`msg ${message.role}`}>
      <div className="avatar">{isUser ? <User size={14} /> : <Sparkles size={14} />}</div>
      <div className="bubble">
        {isUser ? (
          editing ? (
            <MessageEditor message={message} onClose={() => setEditing(false)} />
          ) : (
            <div className="user-bubble"><div className="user-text">{message.content}</div></div>
          )
        ) : (
          <div className="markdown">
            {message.reasoning && <Reasoning text={message.reasoning} live={streaming && !message.content} />}
            <BodyBoundary resetKey={message.id}>
              {message.tool_events && message.tool_events.length > 0 && <ToolEvents events={message.tool_events} conversationId={message.conversation_id} streaming={streaming} />}
              {message.content ? (
                <MarkdownPreview source={message.content} streaming={streaming} />
              ) : streaming && !message.reasoning && !message.tool_events?.some((t) => t.pending) ? (
                <span className="thinking"><span /><span /><span /></span>
              ) : null}
            </BodyBoundary>
            {streaming && message.content && <span className="cursor" />}
          </div>
        )}
        {message.error && <div className="msg-error"><AlertCircle size={14} /><span>{message.error}</span></div>}
        {bare ? <div className="msg-partial">Stopped before any output</div> : note && <div className="msg-partial">{note}</div>}
        {last && !streaming && message.role === 'assistant' && message.error && message.error_kind && <div className="msg-error-actions"><ErrorAction conversationId={message.conversation_id} kind={message.error_kind} /></div>}
        {last && !streaming && message.role === 'assistant' && <ContinueButton conversationId={message.conversation_id} messageId={message.id} />}
        {!streaming && message.role === 'assistant' && message.tool_events?.some((t) => FILE_CHANGING.test(t.name)) && <FilesChanged messageId={message.id} />}
        {!streaming && !editing && (
          <div className="msg-actions">
            {message.model && (
              <span className="model-tag" title={message.model === modelLabel(message.model) ? undefined : message.model}>
                {modelLabel(message.model)}
              </span>
            )}
            {ctx && ctxCount > 0 && (
              <button className="ctx-chip" title="Context used for this reply" onClick={() => { const s = useStore.getState(); if (!s.contextOpen) s.toggleContext() }}>
                {ctx.memories.length > 0 && <span><Brain size={11} />{ctx.memories.length}</span>}
                {ctx.nodes.length > 0 && <span><Share2 size={11} />{ctx.nodes.length}</span>}
                {ctx.chunks.length > 0 && <span><FileText size={11} />{ctx.chunks.length}</span>}
              </button>
            )}
            {!isUser && (message.tool_events?.length ?? 0) > 0 && (
              <SaveSkill conversationId={message.conversation_id} messageId={message.id} />
            )}
            {trace && (
              <button className="ctx-chip" title="Execution trace: LLM rounds, tool calls, timings and tokens" onClick={() => useStore.getState().openTrace(message.id)}>
                <span><Activity size={11} />{trace.steps} step{trace.steps === 1 ? '' : 's'} · {fmtMs(trace.total_ms)}{trace.tokens ? ` · ${trace.tokens.toLocaleString()} tok` : ''}</span>
              </button>
            )}
            {!bare && <CopyButton text={message.content} />}
            {editable && isUser && (
              <button type="button" className="ctx-chip" title="Edit and resend: this message and everything after it is hidden" aria-label="Edit message" onClick={() => setEditing(true)}>
                <Pencil size={11} />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
})

export default MessageView
