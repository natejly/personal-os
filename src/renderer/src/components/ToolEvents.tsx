import { memo, useEffect, useMemo, useState } from 'react'
import { ChevronRight, MessageSquare, Globe, FileSearch, Brain, Share2, Terminal, Clock, Wrench, AlertCircle, Laptop, Zap, ListChecks, PenLine, ShieldAlert, ShieldCheck,
  FolderOpen, FileText, FilePen, Trash2, PackageCheck, CircleHelp, CircleCheck,
  Youtube, Github, Rss, Undo2, Bot, Eye, FileOutput, PanelRight, BookOpen, Download, MousePointerClick, Keyboard, ListFilter, ArrowDownUp, MonitorCog, Package, Search, Copy, FolderPlus, OctagonX, Hourglass, ShieldQuestion, CalendarDays, CalendarClock, CalendarSearch, CalendarPlus, CalendarX } from 'lucide-react'
import type { DocRevision, RunTapeEvent, ToolEvent, Verification } from '@shared/types'
import { api } from '../lib/api'
import { useStore } from '../store'
import DiffView from './DiffView'
import PlanApproval from './PlanApproval'
import RenderBoundary from './RenderBoundary'
import ApprovalRules from './ApprovalRules'
import { describeCall, errorLine, fmtMs, groupSummary, partitionEvents, QUESTION_TOOLS, recalledChats } from '../lib/toolDisplay'
import { AskQuestion } from './DeskApprovalCard'
import { GenericApproval, GenericBody } from './toolcards/GenericCard'
import { OutputFiles } from './toolcards/parts'
// Importing the index registers every dedicated card (TaskCard, FileCard, and whatever other workstreams add).
import { TOOL_CARDS } from './toolcards'
import { latestBrowserCall } from '../lib/browserApproval'
// The ask card mounts inline in a chat bubble, so it needs the sheet the desk panes use.
import '../styles/cowork.css'
import '../styles/docs.css'
import Face from './Face'
import { reviewLine, reviewTitle } from '../lib/approvalHistory'

const ICONS: Record<string, JSX.Element> = {
  propose_plan: <ListChecks size={13} />, show: <PanelRight size={13} />,
  agent_spawn: <Bot size={13} />, agent_wait: <Bot size={13} />, agent_stop: <Bot size={13} />, desk_start: <FolderOpen size={13} />,
  calendar_events: <CalendarDays size={13} />, calendar_get: <CalendarDays size={13} />, calendar_free_busy: <CalendarClock size={13} />,
  calendar_find_time: <CalendarSearch size={13} />, calendar_propose: <CalendarDays size={13} />, calendar_create: <CalendarPlus size={13} />,
  calendar_update: <CalendarClock size={13} />, calendar_delete: <CalendarX size={13} />,
  desk_list_files: <FolderOpen size={13} />, desk_read_file: <FileText size={13} />, desk_write_file: <FilePen size={13} />,
  desk_trash_file: <Trash2 size={13} />, desk_deliver: <PackageCheck size={13} />, desk_ask: <CircleHelp size={13} />, ask_user: <CircleHelp size={13} />,
  desk_done: <CircleCheck size={13} />, desk_import_sandbox: <FolderOpen size={13} />,
  web_search: <Globe size={13} />, fetch_url: <Globe size={13} />, open_page: <Globe size={13} />,
  youtube_video: <Youtube size={13} />, youtube_search: <Youtube size={13} />,
  github_search: <Github size={13} />, github_read: <Github size={13} />, read_feed: <Rss size={13} />,
  find_files: <Laptop size={13} />, read_local_file: <Laptop size={13} />,
  write_local_file: <Laptop size={13} />, move_local_file: <Laptop size={13} />, trash_local_file: <Laptop size={13} />, list_shortcuts: <Zap size={13} />, run_shortcut: <Zap size={13} />,
  search_documents: <FileSearch size={13} />, read_document: <FileSearch size={13} />, list_documents: <FileSearch size={13} />,
  doc_list: <PenLine size={13} />, doc_search: <PenLine size={13} />, doc_read: <PenLine size={13} />,
  doc_create: <PenLine size={13} />, doc_edit: <PenLine size={13} />, doc_delete: <Trash2 size={13} />,
  doc_comments: <PenLine size={13} />, doc_comment_reply: <PenLine size={13} />,
  search_memory: <Brain size={13} />, save_memory: <Brain size={13} />,
  graph_search: <Share2 size={13} />, graph_traverse: <Share2 size={13} />, graph_add: <Share2 size={13} />,
  run_python: <Terminal size={13} />, current_time: <Clock size={13} />,
  sandbox_exec: <Terminal size={13} />, sandbox_write_file: <Terminal size={13} />, sandbox_read_file: <Terminal size={13} />,
  sandbox_list_files: <Terminal size={13} />, sandbox_put_document: <Terminal size={13} />, sandbox_export_file: <Terminal size={13} />, sandbox_reset: <Terminal size={13} />,
  sandbox_checkpoint: <Terminal size={13} />, sandbox_restore: <Terminal size={13} />,
  shell_run: <Terminal size={13} />, opencode_run: <Bot size={13} />, shell_poll: <Hourglass size={13} />, shell_kill: <OctagonX size={13} />, python_install: <Package size={13} />,
  fs_glob: <Search size={13} />, fs_grep: <FileSearch size={13} />, fs_edit: <FilePen size={13} />, fs_copy: <Copy size={13} />, fs_mkdir: <FolderPlus size={13} />,
  desk_fetch_file: <Download size={13} />, todo_write: <ListChecks size={13} />,
  browser_open: <Globe size={13} />, browser_snapshot: <BookOpen size={13} />, browser_click: <MousePointerClick size={13} />,
  browser_type: <Keyboard size={13} />, browser_select: <ListFilter size={13} />, browser_press: <Keyboard size={13} />,
  browser_scroll: <ArrowDownUp size={13} />, browser_manage: <MonitorCog size={13} />, browser_handoff: <MonitorCog size={13} />, browser: <ShieldQuestion size={13} />,
  view_image: <Eye size={13} />, convert_document: <FileOutput size={13} />, render_preview: <FileOutput size={13} />, doc_guide: <BookOpen size={13} />
}

/** The read-back verdict the backend put on the result (verify.py). It rides in result_preview,
 *  which is also what the stored tool-event row keeps, so an old reply still shows how its writes
 *  were proven. An unverified write already carries `error`, so this only has to label it. */
function verdict(t: ToolEvent): Verification | null {
  if (!t.result_preview) return null
  try {
    const v = (JSON.parse(t.result_preview) as { verification?: Verification }).verification
    return v && typeof v.status === 'string' ? v : null
  } catch {
    return null
  }
}

/** Badge for how an external write was proved, naming the fields that were compared. */
function Verdict({ event }: { event: ToolEvent }): JSX.Element | null {
  const v = verdict(event)
  if (!v) return null
  const tries = `${v.attempts} read-back${v.attempts === 1 ? '' : 's'}`
  return (
    <span className={`tag ${v.status === 'verified' ? 'verified' : 'unproven'}`}
      title={`${v.what} · compared ${v.compared.join(', ') || 'existence'} · ${tries}`}>
      {v.status === 'verified' ? <ShieldCheck size={11} /> : <ShieldAlert size={11} />} {v.status}
    </span>
  )
}

function parseDocEdit(preview: string): { revision_id: string; doc_id: string } | null {
  try {
    const o = JSON.parse(preview) as { revision_id?: unknown; doc_id?: unknown }
    if (typeof o.revision_id !== 'string') return null
    return { revision_id: o.revision_id, doc_id: typeof o.doc_id === 'string' ? o.doc_id : '' }
  } catch {
    return null
  }
}

/** The diff for a doc_edit, under the tool row. Ask mode can accept or reject it here. */
function DocEditDiff({ preview }: { preview: string }): JSX.Element | null {
  const info = useMemo(() => parseDocEdit(preview), [preview])
  const [rev, setRev] = useState<DocRevision | null>(null)
  const [current, setCurrent] = useState<string | undefined>(undefined)
  const [missing, setMissing] = useState(false)
  const acceptRevision = useStore((s) => s.acceptRevision)
  const rejectRevision = useStore((s) => s.rejectRevision)

  useEffect(() => {
    if (!info) return
    let dead = false
    setMissing(false)
    void (async () => {
      try {
        const r = await api.docs.revision(info.revision_id)
        if (dead) return
        let cur: string | undefined
        if (r.status === 'pending') {
          const doc = await api.docs.get(info.doc_id || r.doc_id).catch(() => null)
          if (dead) return
          cur = doc?.content
        }
        setCurrent(cur)
        setRev(r.status === 'pending' && cur !== undefined && cur !== r.before ? { ...r, stale: true } : r)
      } catch {
        if (!dead) setMissing(true)
      }
    })()
    return () => { dead = true }
  }, [info])

  if (!info || missing) return null
  if (!rev) return <p className="muted small tool-doc-diff">Loading diff…</p>

  const pending = rev.status === 'pending'
  const reload = async (): Promise<void> => {
    const r = await api.docs.revision(info.revision_id).catch(() => null)
    if (r) setRev(r)
    setCurrent(undefined)
  }
  return (
    <div className="tool-doc-diff">
      <DiffView
        revision={rev}
        current={pending ? current : undefined}
        collapsed
        onAccept={pending ? () => { void acceptRevision(rev.id).then(reload) } : undefined}
        onReject={pending ? () => { void rejectRevision(rev.id).then(reload) } : undefined}
      />
    </div>
  )
}

/** The subagent ids a spawn / wait / stop result names. The preview may be cut, so this reads ids out of the text. */
export function agentIds(preview: string): string[] {
  const out: string[] = []
  for (const m of preview.matchAll(/agent_id\\?"\s*:\s*\\?"(sa_[0-9a-f]+)/g)) if (!out.includes(m[1])) out.push(m[1])
  return out
}

const AGENT_DONE = ['done', 'error', 'interrupted']

/** One child run: live status while it works, then its recorded calls on expand. */
function AgentRunCard({ id }: { id: string }): JSX.Element {
  const [status, setStatus] = useState('running')
  const [cost, setCost] = useState<number | null>(null)
  const [open, setOpen] = useState(false)
  const [tape, setTape] = useState<RunTapeEvent[] | null>(null)
  useEffect(() => {
    let dead = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async (): Promise<void> => {
      try {
        const r = await api.agentRun(id)
        if (dead) return
        setStatus(r.status)
        setCost(typeof r.budget?.cost === 'number' ? r.budget.cost : null)
        if (!AGENT_DONE.includes(r.status)) timer = setTimeout(() => void poll(), 2000)
      } catch { /* the row may not exist yet; try again */ if (!dead) timer = setTimeout(() => void poll(), 3000) }
    }
    void poll()
    return () => { dead = true; if (timer) clearTimeout(timer) }
  }, [id])
  useEffect(() => {
    if (!open) return
    let dead = false
    const load = (): void => { void api.agentTape(id).then((t) => { if (!dead) setTape(t) }).catch(() => undefined) }
    load()
    const iv = AGENT_DONE.includes(status) ? undefined : setInterval(load, 2000)
    return () => { dead = true; if (iv) clearInterval(iv) }
  }, [open, id, status])
  return (
    <div className="tool-doc-diff agent-run">
      <div className="agent-run-head">
      <button className="tool-head" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <ChevronRight size={12} className={open ? 'rot90' : ''} />
        <Face name={id} status={status} size={18} />
        <span className="tool-name">subagent {id.slice(-4)}</span>
        <span className={`tag ${status === 'error' ? 'unproven' : ''}`}>{status === 'awaiting_approval' ? 'needs approval' : status}</span>
        {cost !== null && cost > 0 && <span className="tool-ms">${cost.toFixed(3)}</span>}
      </button>
      <button className="icon-btn ghost agent-talk" aria-label="Open this subagent" title="Read its transcript and message it"
        onClick={() => useStore.getState().openSubagent(id)}><MessageSquare size={13} /></button>
      </div>
      {open && (
        <div className="tool-body">
          {!tape ? <p className="muted small">Loading…</p> : tape.filter((e) => e.event === 'tool_call' || e.event === 'tool_result').length === 0
            ? <p className="muted small">No tool calls.</p>
            : tape.filter((e) => e.event === 'tool_call' || e.event === 'tool_result').map((e) => (
              <div key={e.seq} className="muted small">
                {e.event === 'tool_call' ? '→ ' : '← '}{String(e.data.name ?? '')}{' '}
                {e.event === 'tool_call' ? JSON.stringify(e.data.arguments ?? {}).slice(0, 140) : String(e.data.error ?? e.data.result_preview ?? '').slice(0, 140)}
              </div>
            ))}
        </div>
      )}
    </div>
  )
}

/**
 * Undo for a file the agent wrote or moved, or for a calendar / Google Tasks write. A changed file asks before it is
 * overwritten; a changed event or task is never overwritten. This is the user's action, never the model's.
 */
function UndoButton({ undo }: { undo: NonNullable<ToolEvent['undo']> }): JSX.Element {
  const [state, setState] = useState<'idle' | 'busy' | 'restored'>('idle')
  const toast = useStore((s) => s.toast)
  const external = undo.external_id
  const go = async (force: boolean): Promise<void> => {
    setState('busy')
    try {
      if (external) await api.undoExternal(external)
      else await api.restoreFileSnapshot(undo.snapshot_id ?? '', force)
      setState('restored')
    } catch (e) {
      let info: { reason?: string; conflict?: boolean } = {}
      try { info = JSON.parse((e as Error).message) } catch { /* plain message */ }
      if (!external && info.conflict && window.confirm('That file changed since the assistant wrote it. Restore the earlier version anyway?')) return go(true)
      if (/restored|undone/.test(info.reason ?? '')) setState('restored')
      else {
        setState('idle')
        toast(info.reason ?? (e as Error).message, 'error')
      }
    }
  }
  return state === 'restored'
    ? <span className="tag tool-undo">{external ? 'Undone' : 'Restored'}</span>
    : <button className="ghost-btn sm tool-undo" disabled={state === 'busy'} onClick={() => void go(false)}
        title={undo.notifies ? 'Guests are emailed about the undo, as they were about the change' : undefined}>
        <Undo2 size={12} /> Undo{undo.notifies ? ' (emails guests)' : ''}
      </button>
}

const undoable = (t: ToolEvent): boolean => !t.pending && !t.error && !!(t.undo?.snapshot_id || t.undo?.external_id)

/**
 * What a tool row degrades to when its card throws. It depends on nothing that could have thrown (no
 * describeCall, no card code) and shows the raw call. While the call waits on approval the decision stays
 * reachable as a one-shot click: Deny always, Allow except for the two calls whose approval carries a
 * payload (plan steps, a typed answer). No standing grants, no edited arguments, and nothing decides on its own.
 */
function ToolFallback({ event, conversationId }: { event: ToolEvent; conversationId: string }): JSX.Element {
  let args = ''
  try { args = JSON.stringify(event.arguments, null, 2) ?? '' } catch { args = '' }
  const asking = !!event.pending && !!event.needs_approval
  const decide = (d: 'allow' | 'deny'): void => { void useStore.getState().approveTool(event.id, d, conversationId) }
  return (
    <div className="tool-event error">
      <div className="tool-head">
        <span className="tool-icon"><AlertCircle size={13} /></span>
        <span className="tool-name">{event.name}</span>
        <span className="tool-summary">This tool call could not be displayed</span>
      </div>
      {args && <pre className="render-fallback">{args}</pre>}
      {asking && (
        <div className="aplan-actions">
          <button className="ghost-btn" onClick={() => decide('deny')}>Deny</button>
          {event.name !== 'propose_plan' && !QUESTION_TOOLS.has(event.name) && <button className="primary-btn" onClick={() => decide('allow')}>Allow once</button>}
        </div>
      )}
    </div>
  )
}

/** Runs a row's render inside its boundary, so a throw while describing the call lands in that row's fallback. */
function Row({ render }: { render: () => JSX.Element }): JSX.Element {
  return render()
}

const hasCard = (t: ToolEvent): boolean => t.name !== 'propose_plan' && !(QUESTION_TOOLS.has(t.name) && !!t.pending && !!t.needs_approval) && !!TOOL_CARDS[t.name]

/** `browserSession`: set on the transcript's latest reply that used the browser; its last browser card offers the viewer. */
function ToolEvents({ events, conversationId, streaming = false, browserSession }: { events: ToolEvent[]; conversationId: string; streaming?: boolean; browserSession?: string }): JSX.Element {
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [groupOpen, setGroupOpen] = useState<Record<string, boolean>>({})
  const approveTool = useStore((s) => s.approveTool)
  const tools = useStore((s) => s.tools)
  const lastBrowser = browserSession ? latestBrowserCall(events) : null
  // The context panel only ever shows the main view's chat, so "See why" is offered there alone.
  const inMainChat = useStore((s) => s.view === 'chat' && s.focusedConversationId === conversationId)
  // A chat working autonomously shows its desk's plan card at the foot of the transcript (DeskInline), so not twice.
  const deskPlanShown = useStore((s) => inMainChat && s.activeDesk?.conversation_id === conversationId && !!s.activeDesk.plan)
  const decideFor = (t: ToolEvent) => async (approve: boolean, edited?: Record<string, unknown>): Promise<void> =>
    approveTool(t.id, approve ? 'allow' : 'deny', conversationId, edited ? { arguments: edited } : undefined)

  /** The generic row: a header, then whatever the call produced. Used for every tool without a dedicated card. */
  function genericRow(t: ToolEvent): JSX.Element {
    const d = describeCall(t.name, t.arguments)
    return (
      <div className={`tool-event ${t.pending ? 'pending' : ''} ${t.pending && t.needs_approval ? 'awaiting' : ''} ${t.error ? 'error' : ''}`}>
        <button className="tool-head" title={tools.find((x) => x.name === t.name)?.description || t.name} aria-expanded={!!open[t.id]} onClick={() => setOpen((o) => ({ ...o, [t.id]: !o[t.id] }))}>
          <ChevronRight size={12} className={open[t.id] ? 'rot90' : ''} />
          <span className="tool-icon">{ICONS[t.name] ?? <Wrench size={13} />}</span>
          <span className="tool-name human">{d.verb}</span>
          {t.agent && <span className="tag" title="Raised by a subagent">via {t.agent}</span>}
          <span className="tool-summary">{d.subject}</span>
          <Verdict event={t} />
          {t.plan ? (
            <span className="tag plan" title={`Approved in the plan "${t.plan.title || 'untitled'}" (step ${t.plan.idx + 1})`}>in plan</span>
          ) : t.approval && t.approval !== 'allow' && <span className="tag">{t.approval === 'deny' ? 'denied' : 'approved'}</span>}
          {t.pending ? (t.needs_approval ? <span className="tag ask">needs approval</span> : <span className="thinking mini"><span /><span /><span /></span>) : t.error ? <AlertCircle size={12} aria-label="Failed" /> : <span className="tool-ms">{fmtMs(t.duration_ms)}</span>}
        </button>
        {t.error && !open[t.id] && <div className="tool-err">{errorLine(t.error)}</div>}
        {t.images && t.images.length > 0 && (
          <div className="tool-images">
            {t.images.map((im) => (
              <figure key={im.name}>
                <img src={im.data} alt={im.name} />
                <figcaption>{im.name} <a href={im.data} download={im.name}>save</a></figcaption>
              </figure>
            ))}
          </div>
        )}
        {undoable(t) && t.undo && <UndoButton undo={t.undo} />}
        {/* The panel content rides on the event, so a replayed reply can reopen it after the panel was closed. */}
        {t.show && !t.pending && !t.error && (
          <button className="link small" onClick={() => useStore.getState().openShow(conversationId, t.show!)}>Open in side panel</button>
        )}
        {t.name === 'doc_edit' && !t.pending && !t.error && t.result_preview && <DocEditDiff preview={t.result_preview} />}
        {t.name === 'desk_start' && !t.pending && !t.error && /"desk_id":\s*"([^"]+)"/.test(t.result_preview ?? '') && (
          <button className="link small" onClick={() => {
            const id = /"desk_id":\s*"([^"]+)"/.exec(t.result_preview ?? '')?.[1]
            if (id) void useStore.getState().goToDesk(id)
          }}>Open its chat</button>
        )}
        {t.name === 'search_memory' && !t.pending && !t.error && recalledChats(t.result_preview).map((c) => (
          <button key={c.id} className="link small" title="Open this chat" onClick={() => void useStore.getState().selectChat(c.id)}>{c.title}</button>
        ))}
        {t.name.startsWith('agent_') && !t.pending && t.result_preview && agentIds(t.result_preview).map((id) => <AgentRunCard key={id} id={id} />)}
        {t.pending && t.needs_approval && t.name === 'propose_plan' && !deskPlanShown && <PlanApproval event={t} conversationId={conversationId} />}
        {/* A question is answered, not permitted: its options and a text box instead of Allow/Deny. */}
        {t.pending && t.needs_approval && QUESTION_TOOLS.has(t.name) && <AskQuestion event={t} conversationId={conversationId} />}
        {t.pending && t.needs_approval && t.name !== 'propose_plan' && !QUESTION_TOOLS.has(t.name) && (
          <GenericApproval event={t} conversationId={conversationId} decide={async (ok) => decideFor(t)(ok)}
            onWhy={inMainChat ? () => { const s = useStore.getState(); if (!s.contextOpen) s.toggleContext() } : undefined} />
        )}
        {open[t.id] && <GenericBody event={t} />}
      </div>
    )
  }

  function renderEvent(t: ToolEvent): JSX.Element {
    // A dedicated card owns the whole call, pending and finished. It renders from the event alone, so a
    // reload (events replayed from the persisted run) shows the same card. propose_plan / desk_ask stay special.
    // A pending question keeps the answer box below; once it is answered (or running) its card shows the question and choices.
    const Card = hasCard(t) ? TOOL_CARDS[t.name] : undefined
    const asking = !!t.pending && !!t.needs_approval
    return (
      <RenderBoundary key={t.id} label={`tool ${t.name}`} resetKey={t} fallback={() => <ToolFallback event={t} conversationId={conversationId} />}>
        {Card ? (
          <>
            {/* The grants line renders inside the card's own box, so one question has one place to answer it. */}
            <Card event={t} pending={asking} decide={decideFor(t)}
              rules={asking ? <ApprovalRules event={t} conversationId={conversationId} /> : undefined}
              conversationId={conversationId} streaming={streaming} browserSession={t.id === lastBrowser ? browserSession : undefined} />
            {undoable(t) && t.undo && <UndoButton undo={t.undo} />}
          </>
        ) : <Row render={() => genericRow(t)} />}
        {reviewLine(t.review) && <div className="tool-review muted small" title={reviewTitle(t.review)}>{reviewLine(t.review)}</div>}
        <OutputFiles event={t} conversationId={conversationId} />
      </RenderBoundary>
    )
  }

  // Foldable rows are finished and plain (see isFoldable), so a group never hides anything that needs the user.
  const items = partitionEvents(events, (n) => hasCard({ name: n } as ToolEvent))
  return (
    <div className="tool-events">
      {items.map((it) => {
        if (it.kind === 'single') return renderEvent(it.event)
        // Open while the reply streams, folded once it is done or loaded from history; a manual toggle wins.
        const isOpen = groupOpen[it.key] ?? !!streaming
        return (
          <div key={`g-${it.key}`} className="tool-event tool-group">
            <button className="tool-head" aria-expanded={isOpen} onClick={() => setGroupOpen((g) => ({ ...g, [it.key]: !isOpen }))}>
              <ChevronRight size={12} className={isOpen ? 'rot90' : ''} />
              <span className="tool-icon"><ListChecks size={13} /></span>
              <span className="tool-summary">{groupSummary(it.events)}</span>
            </button>
            {isOpen && <div className="tool-group-rows">{it.events.map(renderEvent)}</div>}
          </div>
        )
      })}
    </div>
  )
}

/** The tool rows of a streaming reply hold their identity across text deltas, so this skips those renders. */
export default memo(ToolEvents)
