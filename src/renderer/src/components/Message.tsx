import { Component, memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import ChunkViewer, { type ChunkRef } from './ChunkViewer'
import SourcesList from './SourcesList'
import GrainWave from './GrainWave'
import { citeInfo, openCite, splitSources } from '../lib/remarkCites'
import { AlertCircle, User, FileText, Activity, ChevronRight, Play, Pencil, GitBranch, Trash2 } from '../icons'
import type { Attachment, Message, RunChanges, ToolEvent } from '@shared/types'
import { useStore, useMessageSubagents, useSubagents, useWorkers, useBusy } from '../store'
import { api } from '../lib/api'
import { fetchBlobUrl } from '../features/notes/api'
import ToolEvents, { agentIds } from './ToolEvents'
import MarkdownPreview, { CopyButton } from './MarkdownPreview'
import { ShowCtx } from './ShowButton'
export { SAFE_MD } from './MarkdownPreview'
import { traceSummary, fmtMs } from './TraceView'
import { replyMetrics } from '../lib/replyMetrics'
import { runtimeChip, runtimeReasons } from '../lib/runtime'
import { compactCount } from '../lib/usageFormat'
import { parseChatMessage } from '../lib/chatLink'
import { CORE, registerSlot, type MessageSlotProps } from '../extensions/host'
import { Slot } from '../extensions/slot'
import { displayText } from '../extensions/events'
import { parseQuotedMessage } from '../lib/selectionActions'
import { modelLabel } from '../lib/modelLabel'
import { outcomeLabel } from '../lib/outcomeLabel'
import { staysVisible, traceLines } from '../lib/toolDisplay'
import { quietEvents } from '../lib/orchestration'
import { errorAction } from '../lib/errorAction'
import MessageEditor from './MessageEditor'
import { APPROVAL_WAIT, busyText, nowText, waitText } from '../lib/runStatus'
import { clockTime, fullTime } from '../lib/chatMeta'
import Face from './Face'
import ResearchTrail from './ResearchTrail'
import { trailFromEvents } from '../lib/researchTrail'
import { isNoReply, stripNoReply } from '../lib/noReply'

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

/**
 * A reply's trace as one short gray line: the newest step (thinking summary, then each quiet tool call)
 * replaces the one before it, and the live step shimmers.
 */
function ReplyActivity({ reasoning, events, conversationId, streaming, answering }: { reasoning?: string | null; events: ToolEvent[]; conversationId: string; streaming: boolean; answering: boolean }): JSX.Element {
  const subs = useSubagents(conversationId)
  const live = streaming && !answering
  const lines = traceLines(reasoning, events)
  // The live step is the call in flight (or its subagents) when there is one, else the newest summary line.
  const now = live ? nowText({ tool_events: events, content: '' }, subs) : null
  if (!lines.length) return <></>
  const line = (l: (typeof lines)[number], i: number): JSX.Element => {
    const isLive = live && i === lines.length - 1
    return <span key={i} className={`trace-line ${l.error ? 'err' : ''} ${isLive ? 'shimmer' : ''}`}>{isLive && now && !l.error ? now : l.text}</span>
  }
  return (
    <div className="trace" aria-live={live ? 'polite' : undefined}>
      {line(lines[lines.length - 1], lines.length - 1)}
    </div>
  )
}

/**
 * The children a reply spawned, indented under it: live from the stream while they run, and from the spawn
 * results once they are recorded. Open while any child runs; one click on a row opens its transcript.
 */
function SubagentThread({ messageId, conversationId, events }: { messageId: string; conversationId: string; events: ToolEvent[] }): JSX.Element | null {
  const subs = useMessageSubagents(conversationId, messageId)
  const ids = useMemo(() => {
    const out = Object.keys(subs)
    for (const t of events) if (t.name === 'agent_spawn' && t.result_preview) for (const id of agentIds(t.result_preview)) if (!out.includes(id)) out.push(id)
    return out
  }, [subs, events])
  const running = ids.some((id) => subs[id]?.state === 'running')
  const [open, setOpen] = useState<boolean | null>(null) // null: follow the children
  if (!ids.length) return null
  const shown = open ?? running
  return (
    <div className="subagent-thread">
      <button className="subagent-head" onClick={() => setOpen(!shown)} aria-expanded={shown}>
        <ChevronRight size={12} className={shown ? 'rot90' : ''} />
        <span>{ids.length} subagent{ids.length === 1 ? '' : 's'}{running ? ' running' : ''}</span>
      </button>
      {shown && ids.map((id) => {
        const s = subs[id]
        return (
          <button key={id} className="subagent-row" aria-label={`Open subagent ${id.slice(-4)}`} onClick={() => useStore.getState().openSubagent(id)}>
            <Face name={id} status={s?.state ?? 'completed'} size={16} />
            <span className="subagent-role">{s?.role ?? 'subagent'}</span>
            <span className="subagent-now">{s ? s.now || s.state : 'finished'}</span>
            {s && <span className="subagent-meta">{s.rounds} rounds · {s.calls} calls</span>}
          </button>
        )
      })}
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
  const btn = useRef<HTMLButtonElement>(null)
  const act = errorAction(kind)
  if (!act) return null
  const st = (): ReturnType<typeof useStore.getState> => useStore.getState()
  const go = (): void => {
    if (act.action === 'retry') void st().regenerate(conversationId)
    else if (act.action === 'settings') st().openSettings('model')
    else if (act.action === 'models') {
      // The model menu lives under this chat's composer (not another window's); settings is the fallback when none is mounted.
      const trigger = btn.current?.closest('.chat, .win, .quick-ask')?.querySelector<HTMLButtonElement>('.model-menu-trigger')
      if (trigger) trigger.click()
      else st().openSettings('model')
    } else {
      setBusy(true)
      api.compactConversation(conversationId)
        .then(() => st().regenerate(conversationId), (e) => st().toast(`Could not compact: ${(e as Error).message}`, 'error'))
        .catch((e) => st().toast((e as Error).message, 'error'))
        .finally(() => setBusy(false))
    }
  }
  return <button ref={btn} className="ghost-btn" disabled={busy} onClick={go}>{busy ? 'Working…' : act.label}</button>
}

// Tools that can change a granted folder; a reply without one never asks the backend for a change list.
const FILE_CHANGING = /^(write_local_file|move_local_file|trash_local_file|shell_run|opencode_run|fs_edit|fs_copy|fs_mkdir|desk_write_file|desk_trash_file)$/

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

/**
 * The named line for a streaming reply that is busy without streaming text (a tool, a retry or compaction wait, a
 * hand-off, workers, an approval): what it is doing and, for a slow call, for how long. Its 1s timer lives here, so
 * nothing above re-renders. Renders nothing when `busyText` has nothing to say.
 */
function BusyLine({ message }: { message: Message }): JSX.Element | null {
  const subs = useSubagents(message.conversation_id)
  const workers = useWorkers(message.conversation_id)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  const text = busyText(message, { nowMs: now, subs, workers })
  return text ? <StatusLine className="thinking run-status busy-line shimmer" text={text} urgent={text === APPROVAL_WAIT} /> : null
}

/**
 * A line that ticks every second: only its words are live, so a screen reader hears "Running shell" once rather
 * than every second of it. A card waiting on the user is announced at once (`urgent`).
 */
function StatusLine({ className, text, urgent }: { className: string; text: string; urgent?: boolean }): JSX.Element {
  const m = / \d+s$/.exec(text)
  const words = m ? text.slice(0, m.index) : text
  return (
    <div className={`${className} status-wave`}>
      <GrainWave />
      <span role={urgent ? 'alert' : 'status'}>{words}</span>{m && <span aria-hidden="true">{m[0]}</span>}
    </div>
  )
}

/** When each chat's wait began, while a Thinking line shows for it: the chat's placeholder hands over to the reply row's own. */
const thinkingSince = new Map<string, number>()

/**
 * A shimmering "Thinking…" for a reply with nothing to show yet; past 5s it gains the elapsed time, so a slow model does
 * not look hung. `chat` keys the start time, so the count carries on (rather than restarting) when the reply row takes over.
 */
export function Thinking({ chat }: { chat?: string }): JSX.Element {
  // Read at render, before the line it replaces has unmounted.
  const [start] = useState(() => (chat && thinkingSince.get(chat)) || Date.now())
  const [now, setNow] = useState(start)
  useEffect(() => {
    if (chat) thinkingSince.set(chat, start)
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => {
      clearInterval(t)
      if (chat && thinkingSince.get(chat) === start) thinkingSince.delete(chat)
    }
  }, [chat, start])
  const text = waitText(now - start)
  return <StatusLine className="thinking run-status shimmer" text={text ?? 'Thinking…'} />
}

/** The "N steps · ms · tok" chip, shown only with Settings → Behavior → Developer tools on. Its own component so the
 *  subscription stays out of MessageView. */
function TraceChip({ message, showContextChips }: MessageSlotProps): JSX.Element | null {
  const devTools = useStore((s) => s.settings.devTools === true)
  if (!showContextChips || !devTools || !message.trace?.length) return null
  const trace = traceSummary(message.trace)
  return (
    <button className="ctx-chip" title="Execution trace: LLM rounds, tool calls, timings and tokens" onClick={() => useStore.getState().openTrace(message.id)}>
      <span><Activity size={11} />{trace.steps} step{trace.steps === 1 ? '' : 's'} · {fmtMs(trace.total_ms)}{trace.tokens ? ` · ${trace.tokens.toLocaleString()} tok` : ''}</span>
    </button>
  )
}

/** Which runtime slots a provider other than the built-in served this reply, with the reasons on hover. Nothing for a reply that used only the defaults. */
function RuntimeChip({ message }: MessageSlotProps): JSX.Element | null {
  const used = message.context_used?.runtime
  if (!used?.length) return null
  return <span className="model-tag" title={runtimeReasons(used)}>{runtimeChip(used)}</span>
}

/** The model name plus, on a finished reply with a trace, its total tokens and generation speed, as one muted line.
 *  It reads no store state, so it stays out of MessageView's render path. */
function ModelTag({ message, streaming }: { message: Message; streaming: boolean }): JSX.Element | null {
  const m = message.role === 'assistant' && !streaming ? replyMetrics(message.trace) : null
  if (!message.model && !m) return null
  const speed = m?.tokPerSec != null ? ` · ${m.tokPerSec.toFixed(1)} tok/s` : ''
  const usage = m ? `${compactCount(m.tokens)} tok${speed}` : ''
  const label = message.model ? modelLabel(message.model) : ''
  const tips = [message.model && message.model !== label ? message.model : '', m ? `${m.tokens.toLocaleString()} tokens used${m.tokPerSec != null ? ` at ${m.tokPerSec.toFixed(1)} tokens/second` : ''}` : ''].filter(Boolean)
  return <span className="model-tag" title={tips.join('\n') || undefined}>{[label, usage].filter(Boolean).join(' · ')}</span>
}

registerSlot(CORE, 'message.footer', { id: 'model-tag', order: 10, Component: ModelTag })
registerSlot(CORE, 'message.footer', { id: 'runtime', order: 15, Component: RuntimeChip })
registerSlot(CORE, 'message.footer', { id: 'trace', order: 20, Component: TraceChip })

// The store is read imperatively inside the handlers: any subscription here defeats the memo, and a
// streamed token would re-render every message in every mounted transcript.
/** `showContextChips`: only ChatView mounts the context drawer, so only it shows chips that open it.
 *  `browserSession`: the agent browser this transcript drives, passed only to its latest reply that used the browser. */
/** The face a reply wears; a chat opened on an agent passes that agent's (see useChatFace), the default is the thread's own. */
export type ChatFace = { name: string; hue?: number; tone?: number }

const MessageView = memo(function MessageView({ message, streaming, last = false, editable = false, resendable = editable, showContextChips = false, branchable = false, browserSession, face, plain = false, from }: { message: Message; streaming: boolean; last?: boolean; editable?: boolean; /** Edit and resend; defaults to `editable`. A desk or job transcript is edit-proof, but a message in it can still be deleted. */ resendable?: boolean; showContextChips?: boolean; branchable?: boolean; browserSession?: string; face?: ChatFace; /** A row not stored as a chat message (a worker's history): no actions under it. */ plain?: boolean; /** A user-role row someone else wrote, labelled with their face and name (the main agent's task to a worker). */ from?: { label: string; face: ChatFace } }): JSX.Element | null {
  const [editing, setEditing] = useState(false)
  const busy = useBusy(message, streaming && message.role === 'assistant')
  const isUser = message.role === 'user'
  // The NO_REPLY marker is never text the user sees: a reply that is only the marker keeps its tool cards, and one that trails it loses the line.
  const sentinel = !isUser && isNoReply(message.content)
  const content = useMemo(() => (isUser ? message.content : stripNoReply(message.content)), [isUser, message.content])
  // What the extensions' display handlers make of the text; copying, citing and editing still use the stored text.
  const shownText = useMemo(() => displayText(message, content), [message, content])
  const chatFrom = useMemo(() => (message.kind === 'chat_in' || message.kind === 'chat_reply' ? parseChatMessage(message.content) : null), [message.kind, message.content])
  const ctx = message.context_used
  // Numbered sources this reply may cite as [n]; rows saved before numbering have no `n` and stay plain text.
  const chunks = ctx?.chunks
  // Chips and the sources footer number by first citation, so a reply citing [4] then [2] reads [1], [2].
  // Keyed on a string: a streamed token that cites nothing new keeps the same map, and the markdown blocks stay memoised.
  const citedKey = useMemo(() => (chunks?.length ? splitSources(content, chunks).cited.map((c) => c.n).join(',') : ''), [content, chunks])
  const cites = useMemo(() => {
    const order = citedKey.split(',').map(Number)
    return new Map((chunks ?? []).filter((c) => c.n).map((c) => [c.n!, { ...citeInfo(c), shown: order.indexOf(c.n!) + 1 || undefined }]))
  }, [chunks, citedKey])
  const [citing, setCiting] = useState<ChunkRef | null>(null)
  const onCite = useCallback((n: number) => { const c = chunks?.find((x) => x.n === n); if (c) openCite(c, setCiting) }, [chunks])
  // An interrupted row carries both an `Interrupted:` error and the outcome; the error line says it once.
  const note = !streaming && message.role === 'assistant' && !message.error ? outcomeLabel(message.outcome) : null
  const bare = !streaming && message.role === 'assistant' && message.outcome === 'stopped' && !content && !message.tool_events?.length && !message.reasoning
  // Calls that need the user (or that the user acts on) stay in place; the rest fold into the activity line.
  // Hand-offs to workers are not cards: the app shows the workers themselves.
  const events = useMemo(() => quietEvents(message.tool_events), [message.tool_events])
  const [shown, folded] = useMemo(() => [(events ?? []).filter(staysVisible), (events ?? []).filter((t) => !staysVisible(t))], [events])
  const trail = useMemo(() => trailFromEvents(events), [events])
  const summarized = !isUser && message.trace?.some((sp) => sp.kind === 'compact' && sp.meta?.kind === 'history')
  // A reply that only handed work on has nothing to draw (the backend removes it once the turn ends); nor does one that is only the sentinel.
  if (!isUser && !streaming && !content && !message.reasoning && !message.error && !message.attachments?.length && !events.length && (message.tool_events?.length || sentinel)) return null
  return (
    <div className={`msg ${message.role}`} data-message-id={message.id} aria-busy={streaming || undefined}>
      {/* The tinted, right-aligned bubble already says "you"; only the assistant gets a face, and each thread its own. */}
      {!isUser && <div className="avatar face-avatar"><Face decor name={face?.name ?? message.conversation_id} hue={face?.hue} tone={face?.tone} status={streaming ? 'streaming' : message.error ? 'error' : undefined} calm={message.outcome === 'stopped'} /></div>}
      <div className="bubble">
        <Slot name="message.header" props={{ message, streaming, showContextChips }} />
        {isUser ? (
          editing ? (
            <MessageEditor message={message} onClose={() => setEditing(false)} />
          ) : (
            <div className="user-bubble">{from && <div className="user-from"><Face decor name={from.face.name} hue={from.face.hue} tone={from.face.tone} size={14} /> {from.label}</div>}<AttachmentChips files={message.attachments} />{message.content && (chatFrom ? <ChatMessageText content={message.content} from={chatFrom} /> : <UserText content={shownText} />)}</div>
          )
        ) : (
          <div className="msg-body">
            <BodyBoundary resetKey={message.id}>
              {(message.reasoning || folded.length > 0) && !(busy && !content) && <ReplyActivity reasoning={message.reasoning} events={folded} conversationId={message.conversation_id} streaming={streaming} answering={!!content} />}
              {trail && <ResearchTrail trail={trail} />}
              {!isUser && <SubagentThread messageId={message.id} conversationId={message.conversation_id} events={events ?? []} />}
              {shown.length > 0 && <ToolEvents events={shown} conversationId={message.conversation_id} streaming={streaming} browserSession={browserSession} />}
              {/* Only the rendered text lives in .markdown: its element rules (p, ul, li) out-rank the
                  single-class rules the cards above are styled with. Its streaming class draws the cursor. */}
              {content ? (
                <div className={streaming ? 'markdown streaming' : 'markdown'}>
                  <ShowCtx.Provider value={message.conversation_id}>
                    <MarkdownPreview source={shownText} streaming={streaming} cites={cites} onCite={onCite} />
                  </ShowCtx.Provider>
                </div>
              ) : streaming && !message.reasoning && !folded.length && !shown.some((t) => t.pending) && !busy ? (
                <Thinking chat={message.conversation_id} />
              ) : null}
              {/* The one named live line, below whatever has streamed: it replaces the trace's live step and the bare Thinking. */}
              {busy && <BusyLine message={message} />}
            </BodyBoundary>
            <ReplyAttachments files={message.attachments} />
            {!streaming && chunks && <SourcesList content={content} chunks={chunks} onOpen={(c) => openCite(c, setCiting)} />}
            {citing && <ChunkViewer chunk={citing} onClose={() => setCiting(null)} />}
          </div>
        )}
        {message.error && <div className="msg-error"><AlertCircle size={14} /><span>{message.error}</span></div>}
        {summarized && showContextChips && (
          <button className="compact-note" title="Open the context panel, where the summary lives" onClick={() => { const s = useStore.getState(); if (!s.contextOpen) s.toggleContext() }}>
            Earlier messages summarized
          </button>
        )}
        {bare ? <div className="msg-partial">Stopped before any output</div> : note && <div className="msg-partial">{note}</div>}
        {last && !streaming && message.role === 'assistant' && message.error && message.error_kind && <div className="msg-error-actions"><ErrorAction conversationId={message.conversation_id} kind={message.error_kind} /></div>}
        {last && !streaming && message.role === 'assistant' && <ContinueButton conversationId={message.conversation_id} messageId={message.id} />}
        {!plain && !streaming && message.role === 'assistant' && message.tool_events?.some((t) => FILE_CHANGING.test(t.name)) && <FilesChanged messageId={message.id} />}
        {/* Always mounted and only hidden while the reply streams: the row's height is reserved, so
            nothing lands below the fold when the stream ends. */}
        {!editing && !plain && (
          <div className={streaming ? 'msg-actions streaming' : 'msg-actions'} aria-hidden={streaming || undefined}>
            {message.created_at > 0 && <time className="msg-time" dateTime={new Date(message.created_at * 1000).toISOString()} title={fullTime(message.created_at)}>{clockTime(message.created_at)}</time>}
            <Slot name="message.footer" props={{ message, streaming, showContextChips }} />
            {!bare && <CopyButton text={content} />}
            {resendable && isUser && !message.kind && (
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
/** The files sent with a user turn. Each opens the stored document; the model read its text inline. */
export function AttachmentChips({ files }: { files?: Attachment[] | null }): JSX.Element | null {
  if (!files?.length) return null
  return (
    <div className="msg-files">
      {files.map((a) => (
        <button key={a.id} className="file-chip" title={`Open ${a.name}`} onClick={() => void useStore.getState().openDoc(a.id)}>
          <FileText size={12} /><span>{a.name}</span>
        </button>
      ))}
    </div>
  )
}

/** An image the assistant attached to its reply, as a click-to-open thumbnail. The raw route needs the app token, so it is fetched into a blob. */
export const isImageAttachment = (a: Attachment): boolean => a.mime.startsWith('image/') && !a.mime.includes('svg')

function ReplyImage({ file }: { file: Attachment }): JSX.Element {
  const [url, setUrl] = useState<string | undefined>()
  useEffect(() => {
    let dead = false
    let made = ''
    fetchBlobUrl(`/documents/${encodeURIComponent(file.id)}/raw`).then((u) => { made = u; if (dead) URL.revokeObjectURL(u); else setUrl(u) }).catch(() => undefined)
    return () => { dead = true; if (made) URL.revokeObjectURL(made) }
  }, [file.id])
  return (
    <button className="reply-image" title={`Open ${file.name}`} onClick={() => void useStore.getState().openDoc(file.id)}>
      <img src={url} alt={file.name} />
    </button>
  )
}

/** The files the assistant sent with a reply (screenshots, charts, documents): images inline in a grid, the rest as chips. */
export function ReplyAttachments({ files }: { files?: Attachment[] | null }): JSX.Element | null {
  if (!files?.length) return null
  const images = files.filter(isImageAttachment)
  const rest = files.filter((a) => !isImageAttachment(a))
  return (
    <>
      {images.length > 0 && <div className="reply-images">{images.map((a) => <ReplyImage key={a.id} file={a} />)}</div>}
      <AttachmentChips files={rest} />
    </>
  )
}

/** A message another chat sent (or answered with): "From <title>" opens that chat, then the body. */
function ChatMessageText({ content, from }: { content: string; from: NonNullable<ReturnType<typeof parseChatMessage>> }): JSX.Element {
  return (
    <>
      <div className="user-from">From <button type="button" className="link-btn" onClick={() => void useStore.getState().selectChat(from.fromChat)}>{from.title}</button></div>
      <UserText content={from.body || content} />
    </>
  )
}

/** A user message's text. A quoted block (see `parseQuotedMessage`) shows as a quote rendered as markdown, not as its raw fence. */
function UserText({ content }: { content: string }): JSX.Element {
  const q = useMemo(() => parseQuotedMessage(content), [content])
  if (!q) return <div className="user-text">{content}</div>
  return (
    <>
      {q.before && <div className="user-text">{q.before}</div>}
      <blockquote className="user-quote"><div className="markdown"><MarkdownPreview source={q.quote} /></div></blockquote>
      {q.after && <div className="user-text">{q.after}</div>}
    </>
  )
}

export function PendingUserMessage({ text, attachments, queued }: { text: string; attachments?: Attachment[]; queued?: number }): JSX.Element {
  return (
    <div className="msg user pending" aria-busy="true">
      <div className="avatar"><User size={14} /></div>
      <div className="bubble"><div className="user-bubble"><AttachmentChips files={attachments} />{text && <UserText content={text} />}</div>
        {queued && <div className="pending-queued">Queued #{queued}</div>}</div>
    </div>
  )
}

export default MessageView
