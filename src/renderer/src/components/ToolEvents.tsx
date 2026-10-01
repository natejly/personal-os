import { useState } from 'react'
import { ChevronRight, Globe, FileSearch, Brain, Share2, Terminal, Clock, Wrench, AlertCircle, Laptop, Zap, ListChecks, ShieldAlert, ShieldCheck,
  FolderOpen, FileText, FilePen, Trash2, PackageCheck, CircleHelp, CircleCheck } from 'lucide-react'
import type { ToolEvent, Verification } from '@shared/types'
import { useStore } from '../store'
import PlanApproval from './PlanApproval'
// The ask card mounts inline in a chat bubble, so it needs the sheet the desk panes use.
import '../styles/cowork.css'

const ICONS: Record<string, JSX.Element> = {
  propose_plan: <ListChecks size={13} />,
  desk_list_files: <FolderOpen size={13} />, desk_read_file: <FileText size={13} />, desk_write_file: <FilePen size={13} />,
  desk_trash_file: <Trash2 size={13} />, desk_deliver: <PackageCheck size={13} />, desk_ask: <CircleHelp size={13} />,
  desk_done: <CircleCheck size={13} />, desk_import_sandbox: <FolderOpen size={13} />,
  web_search: <Globe size={13} />, fetch_url: <Globe size={13} />, open_page: <Globe size={13} />,
  find_files: <Laptop size={13} />, read_local_file: <Laptop size={13} />,
  write_local_file: <Laptop size={13} />, move_local_file: <Laptop size={13} />, trash_local_file: <Laptop size={13} />, list_shortcuts: <Zap size={13} />, run_shortcut: <Zap size={13} />,
  search_documents: <FileSearch size={13} />, read_document: <FileSearch size={13} />, list_documents: <FileSearch size={13} />,
  search_memory: <Brain size={13} />, save_memory: <Brain size={13} />,
  graph_search: <Share2 size={13} />, graph_traverse: <Share2 size={13} />, graph_add: <Share2 size={13} />,
  run_python: <Terminal size={13} />, current_time: <Clock size={13} />,
  sandbox_exec: <Terminal size={13} />, sandbox_write_file: <Terminal size={13} />, sandbox_read_file: <Terminal size={13} />,
  sandbox_list_files: <Terminal size={13} />, sandbox_put_document: <Terminal size={13} />, sandbox_reset: <Terminal size={13} />
}

function summary(t: ToolEvent): string {
  const a = t.arguments ?? {}
  if (t.name === 'propose_plan') {
    const steps = Array.isArray(a.steps) ? a.steps : []
    const title = typeof a.title === 'string' && a.title ? a.title : steps.map((s) => (s as { tool?: string })?.tool ?? '?').join(', ')
    return `${steps.length} ${steps.length === 1 ? 'action' : 'actions'}${title ? ` · ${title}` : ''}`.slice(0, 90)
  }
  const first = a.query ?? a.url ?? a.command ?? a.path ?? a.name ?? a.entity ?? a.content ?? a.document_id ?? (a.code ? String(a.code).split('\n')[0] : '') ?? ''
  const s = String(first ?? '')
  return s.length > 90 ? s.slice(0, 90) + '…' : s
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

function pretty(v: unknown): string {
  if (typeof v === 'string') {
    try { return JSON.stringify(JSON.parse(v), null, 2) } catch { return v }
  }
  return JSON.stringify(v, null, 2)
}

/**
 * `desk_ask`: the agent stopped and wants an answer. The call is gated as a card (plans.decide_call
 * rule 2), so the tool body — the thing that writes `desks.question` and moves the desk to Needs you
 * — has not run yet, and the run is sitting on this approval. The answer therefore has to be the
 * DECISION, not a message: it rides back as the approval's note, which the backend hands to the call
 * as `user_note` (app.py). Posting it as a chat message instead left the approval unanswered and the
 * run waiting here forever, because a run with a viewer attached never parks.
 *
 * The store is read imperatively in the handler, never subscribed to: this mounts inside a streaming
 * message, where any broader subscription re-renders every message on every token (Message.tsx:10).
 */
function AskAnswer({ callId, question, context, conversationId }: {
  callId: string
  question: string
  context?: string
  conversationId: string
}): JSX.Element {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)

  const submit = async (): Promise<void> => {
    if (!text.trim() || sending) return
    setSending(true)
    try {
      // 'allow' only: a question is never a standing grant, so no always_chat/always_global here.
      await useStore.getState().approveTool(callId, 'allow', conversationId, { note: text.trim() })
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="aplan ask">
      <header className="aplan-head"><CircleHelp size={14} /><b>The agent has a question</b></header>
      <p className="aplan-intent">{question}</p>
      {context && <p className="muted small">{context}</p>}
      <div className="aplan-foot">
        <textarea
          className="aplan-answer"
          rows={2}
          placeholder="Answer… (⌘↵ to send)"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void submit() } }}
        />
        <div className="aplan-actions">
          <button className="primary-btn" disabled={!text.trim() || sending} onClick={() => void submit()}>Answer</button>
        </div>
      </div>
    </div>
  )
}

export default function ToolEvents({ events, conversationId }: { events: ToolEvent[]; conversationId: string }): JSX.Element {
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const approveTool = useStore((s) => s.approveTool)
  return (
    <div className="tool-events">
      {events.map((t) => (
        <div key={t.id} className={`tool-event ${t.pending ? 'pending' : ''} ${t.error ? 'error' : ''}`}>
          <button className="tool-head" onClick={() => setOpen((o) => ({ ...o, [t.id]: !o[t.id] }))}>
            <ChevronRight size={12} className={open[t.id] ? 'rot90' : ''} />
            <span className="tool-icon">{ICONS[t.name] ?? <Wrench size={13} />}</span>
            <span className="tool-name">{t.name.replace(/_/g, ' ')}</span>
            <span className="tool-summary">{summary(t)}</span>
            <Verdict event={t} />
            {t.plan ? (
              <span className="tag plan" title={`Approved in the plan "${t.plan.title || 'untitled'}" (step ${t.plan.idx + 1})`}>in plan</span>
            ) : t.approval && t.approval !== 'allow' && <span className="tag">{t.approval === 'deny' ? 'denied' : 'approved'}</span>}
            {t.pending ? (t.needs_approval ? <span className="tag ask">needs approval</span> : <span className="thinking mini"><span /><span /><span /></span>) : t.error ? <AlertCircle size={12} /> : <span className="tool-ms">{t.duration_ms} ms</span>}
          </button>
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
          {t.pending && t.needs_approval && t.name === 'propose_plan' && <PlanApproval event={t} conversationId={conversationId} />}
          {/* A question is answered, not permitted, so desk_ask gets a text box instead of Allow/Deny. */}
          {t.pending && t.needs_approval && t.name === 'desk_ask' && (
            <AskAnswer callId={t.id} conversationId={conversationId}
              question={String((t.arguments as { question?: unknown }).question ?? '')}
              context={String((t.arguments as { context?: unknown }).context ?? '') || undefined} />
          )}
          {t.pending && t.needs_approval && t.name !== 'propose_plan' && t.name !== 'desk_ask' && (
            <div className="approval">
              <div className="approval-text"><b>{t.name.replace(/_/g, ' ')}</b> wants to run. This acts outside the app.</div>
              <pre className="approval-args">{pretty(t.arguments)}</pre>
              <div className="approval-actions">
                <button className="primary-btn" onClick={() => void approveTool(t.id, 'allow', conversationId)}>Allow once</button>
                <button className="ghost-btn" onClick={() => void approveTool(t.id, 'always_chat', conversationId)}>Always in this chat</button>
                <button className="ghost-btn" onClick={() => void approveTool(t.id, 'always_global', conversationId)}>Always</button>
                <button className="ghost-btn danger" onClick={() => void approveTool(t.id, 'deny', conversationId)}>Deny</button>
              </div>
            </div>
          )}
          {open[t.id] && (
            <div className="tool-body">
              <div className="tool-col"><h6>Arguments</h6><pre>{pretty(t.arguments)}</pre></div>
              <div className="tool-col"><h6>{t.error ? 'Error' : 'Result'}</h6><pre>{t.pending ? 'Running…' : pretty(t.error ?? t.result_preview)}</pre></div>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
