import { useEffect, useMemo, useState } from 'react'
import { ChevronRight, Globe, FileSearch, Brain, Share2, Terminal, Clock, Wrench, AlertCircle, Laptop, Zap, ListChecks, PenLine, ShieldAlert, ShieldCheck } from 'lucide-react'
import type { DocRevision, ToolEvent, Verification } from '@shared/types'
import { api } from '../lib/api'
import { useStore } from '../store'
import DiffView from './DiffView'
import PlanApproval from './PlanApproval'
import '../styles/docs.css'

const ICONS: Record<string, JSX.Element> = {
  propose_plan: <ListChecks size={13} />,
  web_search: <Globe size={13} />, fetch_url: <Globe size={13} />, open_page: <Globe size={13} />,
  find_files: <Laptop size={13} />, read_local_file: <Laptop size={13} />,
  write_local_file: <Laptop size={13} />, move_local_file: <Laptop size={13} />, trash_local_file: <Laptop size={13} />, list_shortcuts: <Zap size={13} />, run_shortcut: <Zap size={13} />,
  search_documents: <FileSearch size={13} />, read_document: <FileSearch size={13} />, list_documents: <FileSearch size={13} />,
  doc_list: <PenLine size={13} />, doc_search: <PenLine size={13} />, doc_read: <PenLine size={13} />,
  doc_create: <PenLine size={13} />, doc_edit: <PenLine size={13} />,
  search_memory: <Brain size={13} />, save_memory: <Brain size={13} />,
  graph_search: <Share2 size={13} />, graph_traverse: <Share2 size={13} />, graph_add: <Share2 size={13} />,
  run_python: <Terminal size={13} />, current_time: <Clock size={13} />,
  sandbox_exec: <Terminal size={13} />, sandbox_write_file: <Terminal size={13} />, sandbox_read_file: <Terminal size={13} />,
  sandbox_list_files: <Terminal size={13} />, sandbox_put_document: <Terminal size={13} />, sandbox_reset: <Terminal size={13} />
}

function summary(t: ToolEvent): string {
  const a = t.arguments ?? {}
  if (t.name === 'doc_edit') {
    const s = String(a.summary || a.doc || '')
    return s.length > 90 ? s.slice(0, 90) + '…' : s
  }
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

function pretty(v: unknown): string {
  if (typeof v === 'string') {
    try { return JSON.stringify(JSON.parse(v), null, 2) } catch { return v }
  }
  return JSON.stringify(v, null, 2)
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
          {t.name === 'doc_edit' && !t.pending && !t.error && t.result_preview && <DocEditDiff preview={t.result_preview} />}
          {t.pending && t.needs_approval && t.name === 'propose_plan' && <PlanApproval event={t} conversationId={conversationId} />}
          {t.pending && t.needs_approval && t.name !== 'propose_plan' && (
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
