import { useMemo, useState, type ReactNode } from 'react'
import { AlertCircle } from 'lucide-react'
import type { ToolEvent } from '@shared/types'
import { parseResult, type Fields, type Parsed } from '../../lib/toolResult'
import './toolcards.css'

/** The event's result as fields (whole, or the leading ones when the preview was cut). Memoised on the preview text. */
export function useParsed(event: ToolEvent): Parsed {
  return useMemo(() => (event.pending ? { data: null, cut: false } : parseResult(event.result_preview)), [event.pending, event.result_preview])
}

/** Preformatted text in a scrollable box; past `collapseAt` lines it opens with a "Show all" toggle. `tail` shows the end first. */
export function MonoBlock({ text, label, collapseAt = 12, tail = false, empty }: {
  text: string
  label?: string
  collapseAt?: number
  /** Collapsed form shows the LAST lines (command output) instead of the first (code, descriptions). */
  tail?: boolean
  empty?: string
}): JSX.Element | null {
  const [open, setOpen] = useState(false)
  const lines = text.replace(/\s+$/, '').split('\n')
  if (!text.trim()) return empty ? <div className="tc-muted">{empty}</div> : null
  const over = lines.length > collapseAt
  const shown = open || !over ? lines : tail ? lines.slice(-collapseAt) : lines.slice(0, collapseAt)
  return (
    <div className="tc-monowrap">
      {label && <div className="tc-monolabel">{label}</div>}
      <pre className="tc-mono">{shown.join('\n')}</pre>
      {over && (
        <button type="button" className="tc-more" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? 'Show less' : `Show all ${lines.length} lines${tail ? ` (${lines.length - collapseAt} earlier hidden)` : ''}`}
        </button>
      )}
    </div>
  )
}

/** Small labelled facts on one wrapped line: "Folder  ~/desk/work · Took  1.4 s". Falsy values are skipped. */
export function Meta({ items }: { items: Array<[string, ReactNode | null | undefined | false]> }): JSX.Element | null {
  const rows = items.filter(([, v]) => v !== null && v !== undefined && v !== false && v !== '')
  if (!rows.length) return null
  return (
    <dl className="tc-meta">
      {rows.map(([k, v]) => (
        <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
      ))}
    </dl>
  )
}

export function Badge({ children, tone }: { children: ReactNode; tone?: 'ok' | 'warn' | 'bad' | 'run' }): JSX.Element {
  return <span className={`tc-badge ${tone ?? ''}`}>{children}</span>
}

export function Chips({ items, chosen }: { items: string[]; chosen?: string }): JSX.Element | null {
  if (!items.length) return null
  return (
    <ul className="tc-chips">
      {items.map((o) => <li key={o} className={`tc-chip ${chosen === o ? 'chosen' : ''}`}>{o}</li>)}
    </ul>
  )
}

/** The failure line. CardShell's own result block is hidden on these cards, so each card shows its error here. */
export function ErrorLine({ event }: { event: ToolEvent }): JSX.Element | null {
  if (!event.error) return null
  return <div className="tc-result err inline" role="alert"><AlertCircle size={13} /><span>{event.error}</span></div>
}

/** True when the result had nothing a dedicated card could read: show the generic rendering instead. */
export const unreadable = (p: Parsed, event: ToolEvent): boolean => !event.pending && !event.error && !p.data && !!event.result_preview

export type { Fields }
