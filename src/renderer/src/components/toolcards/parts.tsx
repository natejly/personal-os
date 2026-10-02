import { useState, type ReactNode } from 'react'
import { AlertCircle, CheckCircle2, CircleDashed, Loader2, ShieldAlert, ShieldCheck, XCircle } from 'lucide-react'
import type { ToolEvent } from '@shared/types'
import { argRows, cardStatus, preview, resultView, type ArgRow, type CardStatus } from '../../lib/toolDisplay'
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

/** The raw call, behind a disclosure. The only place JSON is shown. */
export function RawDetails({ event }: { event: ToolEvent }): JSX.Element {
  const edited = event.edited_by === 'user'
  return (
    <details className="tc-details">
      <summary>Details</summary>
      <div className="tc-raw">
        <h6>{edited ? 'Arguments that ran (your edit)' : 'Arguments'}</h6>
        <pre>{pretty(event.arguments)}</pre>
        {edited && event.original_arguments && (<><h6>Arguments the assistant proposed</h6><pre>{pretty(event.original_arguments)}</pre></>)}
        {!event.pending && (<><h6>{event.error ? 'Error' : 'Result'}</h6><pre>{pretty(event.error ?? event.result_preview) || '(empty)'}</pre></>)}
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

export function Section({ children }: { children: ReactNode }): JSX.Element {
  return <div className="tc-section">{children}</div>
}
