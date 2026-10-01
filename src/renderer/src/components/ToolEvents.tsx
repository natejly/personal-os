import { useState } from 'react'
import { ChevronRight, Globe, FileSearch, Brain, Share2, Terminal, Clock, Wrench, AlertCircle, ListChecks, FolderOpen, FileText, FilePen, Trash2, PackageCheck, CircleHelp, CircleCheck, Download } from 'lucide-react'
import type { ToolEvent } from '@shared/types'
import { useStore } from '../store'
// The ask card mounts inline in a chat bubble, so it needs the sheet ActionPlanCard also imports.
import '../styles/cowork.css'

const ICONS: Record<string, JSX.Element> = {
  web_search: <Globe size={13} />, fetch_url: <Globe size={13} />,
  search_documents: <FileSearch size={13} />, read_document: <FileSearch size={13} />, list_documents: <FileSearch size={13} />,
  search_memory: <Brain size={13} />, save_memory: <Brain size={13} />,
  graph_search: <Share2 size={13} />, graph_traverse: <Share2 size={13} />, graph_add: <Share2 size={13} />,
  run_python: <Terminal size={13} />, current_time: <Clock size={13} />,
  sandbox_exec: <Terminal size={13} />, sandbox_write_file: <Terminal size={13} />, sandbox_read_file: <Terminal size={13} />,
  sandbox_list_files: <Terminal size={13} />, sandbox_put_document: <Terminal size={13} />, sandbox_reset: <Terminal size={13} />,
  propose_plan: <ListChecks size={13} />,
  desk_list_files: <FolderOpen size={13} />, desk_read_file: <FileText size={13} />, desk_write_file: <FilePen size={13} />,
  desk_trash_file: <Trash2 size={13} />, desk_deliver: <PackageCheck size={13} />, desk_ask: <CircleHelp size={13} />,
  desk_done: <CircleCheck size={13} />, desk_import_sandbox: <Download size={13} />
}

function summary(t: ToolEvent): string {
  const a = t.arguments ?? {}
  const first = a.query ?? a.url ?? a.command ?? a.path ?? a.entity ?? a.content ?? a.document_id ?? (a.code ? String(a.code).split('\n')[0] : '') ?? ''
  const s = String(first ?? '')
  return s.length > 90 ? s.slice(0, 90) + '…' : s
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
      await useStore.getState().approveTool(callId, 'allow', conversationId, text.trim())
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

/**
 * `hasPlan` is set by `Message.tsx` when the reply carried a `plan` event. The plan card is mounted
 * there, beside this list, because a `propose_plan` tool event carries only the raw arguments — the
 * decided, digested `ActionPlan` lives on the message. Without a plan object (an older transcript, or
 * the card still in flight) the generic approval block stays, so the call is never a dead end.
 */
export default function ToolEvents({ events, conversationId, hasPlan = false }: { events: ToolEvent[]; conversationId: string; hasPlan?: boolean }): JSX.Element {
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const approveTool = useStore((s) => s.approveTool)
  return (
    <div className="tool-events">
      {events.map((t) => {
        const asks = !!t.pending && !!t.needs_approval
        const isPlan = asks && t.name === 'propose_plan' && hasPlan
        const isAsk = asks && t.name === 'desk_ask'
        return (
          <div key={t.id} className={`tool-event ${t.pending ? 'pending' : ''} ${t.error ? 'error' : ''}`}>
            <button className="tool-head" onClick={() => setOpen((o) => ({ ...o, [t.id]: !o[t.id] }))}>
              <ChevronRight size={12} className={open[t.id] ? 'rot90' : ''} />
              <span className="tool-icon">{ICONS[t.name] ?? <Wrench size={13} />}</span>
              <span className="tool-name">{t.name.replace(/_/g, ' ')}</span>
              <span className="tool-summary">{summary(t)}</span>
              {t.plan_step && <span className="tag plan" title="This call is a step of the plan you approved, with the arguments you approved.">in plan</span>}
              {t.off_plan && <span className="tag off-plan" title="The approved plan does not contain this call, so it asks on its own.">not in the plan</span>}
              {t.blocked_by === 'plan_mode' && <span className="tag plan" title="Planning mode is on and nothing has been approved yet.">planning</span>}
              {t.approval && t.approval !== 'allow' && <span className="tag">{t.approval === 'deny' ? 'denied' : 'approved'}</span>}
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
            {isAsk && (
              <AskAnswer
                callId={t.id}
                question={String(t.arguments?.question ?? '')}
                context={t.arguments?.context ? String(t.arguments.context) : undefined}
                conversationId={conversationId}
              />
            )}
            {asks && !isPlan && !isAsk && (
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
        )
      })}
    </div>
  )
}
