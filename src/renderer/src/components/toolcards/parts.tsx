import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AlertCircle, CheckCircle2, CircleDashed, FileDown, FolderOpen, Loader2, ShieldAlert, ShieldCheck, XCircle } from 'lucide-react'
import type { ToolEvent } from '@shared/types'
import { api } from '../../lib/api'
import { outputFiles } from '../../lib/toolResult'
import { appendPage, argRows, cardStatus, displayFullOutput, EMPTY_OUTPUT, preview, resultView, type ArgRow, type CardStatus, type FullOutput } from '../../lib/toolDisplay'
import './toolcards.css'

/** Text that collapses past a few lines, so an email body or file does not take over the chat. */
export function LongText({ text, mono = false }: { text: string; mono?: boolean }): JSX.Element {
  const [open, setOpen] = useState(false)
  const short = preview(text)
  const collapsible = short !== text
  return (
    <div className={`tc-long ${mono ? 'mono' : ''}`}>
      <span>{open || !collapsible ? text : short}</span>
      {collapsible && (
        <button type="button" className="tc-more" onClick={() => setOpen((o) => !o)} aria-expanded={open}>{open ? 'Show less' : 'Show more'}</button>
      )}
    </div>
  )
}

/** Labelled key/value list. */
export function ArgList({ rows, highlight }: { rows: ArgRow[]; highlight?: string[] }): JSX.Element | null {
  if (!rows.length) return null
  return (
    <dl className="tc-args">
      {rows.map((r) => (
        <div key={r.key} className={`tc-arg ${highlight?.includes(r.key) ? 'changed' : ''}`}>
          <dt>{r.label}</dt>
          <dd><LongText text={r.text} /></dd>
        </div>
      ))}
    </dl>
  )
}

export function ArgListOf({ args, highlight }: { args: Record<string, unknown> | null | undefined; highlight?: string[] }): JSX.Element | null {
  return <ArgList rows={argRows(args)} highlight={highlight} />
}

/** The result of a finished call as readable text, a list, or facts: never the raw JSON. */
export function ResultBlock({ event }: { event: ToolEvent }): JSX.Element | null {
  if (event.pending) return null
  if (event.error) {
    return <div className="tc-result err" role="alert"><AlertCircle size={13} /><span>{event.error}</span></div>
  }
  const v = resultView(event.result_preview)
  if (v.kind === 'empty') return null
  return (
    <div className="tc-result">
      {v.kind === 'text' && <LongText text={v.text} />}
      {v.kind === 'facts' && <ArgList rows={v.rows} />}
      {v.kind === 'list' && (
        <>
          <ul className="tc-list">{v.items.map((it, i) => <li key={i}>{it}</li>)}</ul>
          {v.total > v.items.length && <div className="tc-muted">and {v.total - v.items.length} more</div>}
        </>
      )}
    </div>
  )
}

function pretty(v: unknown): string {
  if (typeof v === 'string') {
    try { return JSON.stringify(JSON.parse(v), null, 2) } catch { return v }
  }
  return JSON.stringify(v, null, 2)
}

/**
 * The complete stored output of a call, in a modal. Plain text only: it is third-party content and never goes
 * through markdown or HTML. Pages load 20k characters at a time from the stored row.
 */
function FullOutputModal({ event, onClose }: { event: ToolEvent; onClose: () => void }): JSX.Element {
  const [out, setOut] = useState<FullOutput>(EMPTY_OUTPUT)
  const [total, setTotal] = useState<number | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'gone'>('loading')
  const [copied, setCopied] = useState(false)
  const load = async (from: number): Promise<void> => {
    setState('loading')
    try {
      const page = await api.toolResults.read(event.result_id as string, from, 20000)
      setOut((o) => appendPage(o, page))
      setTotal(page.total_chars)
      setState('ready')
    } catch { setState('gone') }
  }
  useEffect(() => { void load(0) }, [])
  useEffect(() => {
    const k = (e: KeyboardEvent): void => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])
  const copy = async (): Promise<void> => {
    try { await navigator.clipboard.writeText(out.text); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* clipboard blocked */ }
  }
  const complete = !out.hasMore && state === 'ready'
  return createPortal(
    <div className="tc-full-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="tc-full" role="dialog" aria-modal="true" aria-label="Full tool output">
        <header>
          <b className="grow">Full output · {event.name}</b>
          <button type="button" className="ghost-btn" onClick={() => void copy()} disabled={!out.text}>{copied ? 'Copied' : complete ? 'Copy' : 'Copy loaded'}</button>
          <button type="button" className="ghost-btn" onClick={onClose}>Close</button>
        </header>
        {state === 'gone' && !out.text ? <pre>Full output no longer stored.</pre> : <pre>{displayFullOutput(out.text, complete) || (state === 'loading' ? 'Loading…' : '(empty)')}</pre>}
        <footer>
          <span>{out.text.length.toLocaleString()}{total !== null ? ` of ${total.toLocaleString()}` : ''} characters</span>
          {state === 'gone' && out.text && <span>Full output no longer stored.</span>}
          {state === 'ready' && out.hasMore && <button type="button" className="ghost-btn" onClick={() => void load(out.nextOffset)}>Load more</button>}
          {state === 'ready' && !out.hasMore && total !== null && out.text.length < total && <span>Stored output ends here.</span>}
        </footer>
      </div>
    </div>,
    document.body
  )
}

/** The raw call, behind a disclosure. The only place JSON is shown. */
export function RawDetails({ event }: { event: ToolEvent }): JSX.Element {
  const edited = event.edited_by === 'user'
  const [viewing, setViewing] = useState(false)
  return (
    <details className="tc-details">
      <summary>Details</summary>
      <div className="tc-raw">
        <h6>{edited ? 'Arguments that ran (your edit)' : 'Arguments'}</h6>
        <pre>{pretty(event.arguments)}</pre>
        {edited && event.original_arguments && (<><h6>Arguments the assistant proposed</h6><pre>{pretty(event.original_arguments)}</pre></>)}
        {!event.pending && (<><h6>{event.error ? 'Error' : 'Result'}</h6><pre>{pretty(event.error ?? event.result_preview) || '(empty)'}</pre></>)}
        {!event.pending && event.result_id && (
          <button type="button" className="ghost-btn tc-fullbtn" onClick={() => setViewing(true)}>Show full output</button>
        )}
        {viewing && <FullOutputModal event={event} onClose={() => setViewing(false)} />}
      </div>
    </details>
  )
}

const STATUS: Record<CardStatus, { label: string; icon: JSX.Element }> = {
  awaiting: { label: 'Needs your approval', icon: <CircleDashed size={12} /> },
  running: { label: 'Running', icon: <Loader2 size={12} className="spin" /> },
  denied: { label: 'Denied', icon: <XCircle size={12} /> },
  failed: { label: 'Failed', icon: <AlertCircle size={12} /> },
  unverified: { label: 'Unverified', icon: <ShieldAlert size={12} /> },
  verified: { label: 'Done · verified', icon: <ShieldCheck size={12} /> },
  done: { label: 'Done', icon: <CheckCircle2 size={12} /> }
}

export function StatusChip({ event }: { event: ToolEvent }): JSX.Element {
  const s = cardStatus(event)
  const label = s === 'running' && event.approval && event.approval !== 'deny' ? 'Approved · running' : STATUS[s].label
  return <span className={`tc-status ${s}`} role="status">{STATUS[s].icon}{label}</span>
}

const fmtSize = (n: number): string =>
  n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`

/**
 * Files a plain chat's tool saved for the user (sandbox export, run_python outputs/, a browser download), each with
 * Download and Show in Finder. Renders nothing for a result without `outputs`, so it can sit under every tool row.
 */
export function OutputFiles({ event, conversationId }: { event: ToolEvent; conversationId: string }): JSX.Element | null {
  const files = useMemo(() => (event.pending || event.error ? [] : outputFiles(event.result_preview)), [event.pending, event.error, event.result_preview])
  const [problem, setProblem] = useState<string | null>(null)
  if (!files.length) return null
  const run = (fn: () => Promise<unknown>) => (): void => {
    setProblem(null)
    fn().catch((e: Error) => setProblem(e.message))
  }
  const reveal = async (): Promise<void> => {
    const { folder } = await api.conversations.outputs(conversationId)
    if (!(await window.os.data.reveal(folder))) throw new Error('That folder is no longer there.')
  }
  return (
    <div className="tc-outputs" aria-label="Files saved for you">
      {files.map((f) => (
        <div key={f.path} className="tc-output">
          <FileDown size={13} />
          <span className="tc-output-name" title={f.path}>{f.name}</span>
          <span className="tc-muted">{fmtSize(f.size)}</span>
          <button type="button" className="ghost-btn" onClick={run(() => api.conversations.downloadOutput(conversationId, f.path))}>Download</button>
        </div>
      ))}
      <button type="button" className="ghost-btn" onClick={run(reveal)}><FolderOpen size={13} /> Show in Finder</button>
      {problem && <span className="tc-muted" role="alert">{problem}</span>}
    </div>
  )
}

export function Section({ children }: { children: ReactNode }): JSX.Element {
  return <div className="tc-section">{children}</div>
}
