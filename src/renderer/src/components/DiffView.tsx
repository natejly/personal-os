import { useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { Check, Columns2, AlignLeft, Copy, RotateCcw, Sparkles, User, AlertTriangle } from 'lucide-react'
import type { DocRevision } from '@shared/types'
import { diffLines, diffStat, hunks, toUnified, type DiffLine, type Hunk, type WordPart } from '../lib/diff'
import { DIFF_ROW_HEIGHT, rowWindow } from '../lib/diffWindow'

/** Word parts of a reworded line, so only the words that actually moved are marked. */
export function Parts({ parts, text, op }: { parts: WordPart[] | undefined; text: string; op: DiffLine['op'] }): JSX.Element {
  if (!parts) return <>{text}</>
  return (
    <>
      {parts.map((p, i) =>
        p.changed ? <mark key={i} className={op === 'add' ? 'w-add' : 'w-del'}>{p.text}</mark> : <span key={i}>{p.text}</span>
      )}
    </>
  )
}

const SIGN = { same: ' ', add: '+', del: '-' } as const

/**
 * Split view pairs each deletion with the addition that replaced it, so a rewording sits on one row
 * with the old wording on the left and the new on the right.
 */
export function pairLines(lines: DiffLine[]): { left: DiffLine | null; right: DiffLine | null }[] {
  const out: { left: DiffLine | null; right: DiffLine | null }[] = []
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i]
    if (l.op === 'same') {
      out.push({ left: l, right: l })
      continue
    }
    if (l.op === 'del') {
      const dels: DiffLine[] = []
      while (i < lines.length && lines[i].op === 'del') dels.push(lines[i++])
      const adds: DiffLine[] = []
      while (i < lines.length && lines[i].op === 'add') adds.push(lines[i++])
      i--
      for (let k = 0; k < Math.max(dels.length, adds.length); k++) out.push({ left: dels[k] ?? null, right: adds[k] ?? null })
      continue
    }
    out.push({ left: null, right: l })
  }
  return out
}

function UnifiedRow({ line: l }: { line: DiffLine }): JSX.Element {
  return (
    <div className={`diff-row ${l.op}`}>
      <span className="diff-no">{l.oldNo ?? ''}</span>
      <span className="diff-no">{l.newNo ?? ''}</span>
      <span className="diff-sign">{SIGN[l.op]}</span>
      <span className="diff-text"><Parts parts={l.parts} text={l.text} op={l.op} /></span>
    </div>
  )
}

function SplitRow({ left, right }: { left: DiffLine | null; right: DiffLine | null }): JSX.Element {
  return (
    <div className="diff-split-row">
      <span className="diff-no">{left?.oldNo ?? ''}</span>
      <span className={`diff-text ${left ? (left.op === 'same' ? 'same' : 'del') : 'void'}`}>
        {left ? <Parts parts={left.parts} text={left.text} op="del" /> : ''}
      </span>
      <span className="diff-no">{right?.newNo ?? ''}</span>
      <span className={`diff-text ${right ? (right.op === 'same' ? 'same' : 'add') : 'void'}`}>
        {right ? <Parts parts={right.parts} text={right.text} op="add" /> : ''}
      </span>
    </div>
  )
}

/** One display row of the diff: a hunk head, a unified line, or a split pair. `i` keys it across scrolls. */
type Row =
  | { i: number; hunk: number; kind: 'head'; newStart: number }
  | { i: number; hunk: number; kind: 'line'; line: DiffLine }
  | { i: number; hunk: number; kind: 'split'; left: DiffLine | null; right: DiffLine | null }

function DiffRow({ row }: { row: Row }): JSX.Element {
  if (row.kind === 'head') return <div className="diff-hunk-head">@@ line {row.newStart} @@</div>
  if (row.kind === 'line') return <UnifiedRow line={row.line} />
  return <SplitRow left={row.left} right={row.right} />
}

const INITIAL_ROWS = 80

/** The nearest element that actually scrolls, the element itself, or the window. */
function scrollParent(el: HTMLElement): HTMLElement | Window {
  const scrolls = (n: HTMLElement): boolean => {
    const oy = getComputedStyle(n).overflowY
    return (oy === 'auto' || oy === 'scroll') && n.scrollHeight > n.clientHeight + 1
  }
  if (scrolls(el)) return el
  for (let n = el.parentElement; n && n !== document.body && n !== document.documentElement; n = n.parentElement) {
    if (scrolls(n)) return n
  }
  return window
}

/**
 * How many rows to build. The diff body is not always the scroller — a review queue scrolls around
 * it, and a card in the history panel scrolls with its pane — so the nearest scrolling ancestor is
 * measured, and its scroll offset says which rows are on screen. A scroll listener on the window in
 * the capture phase hears every scroller, so no listener is attached to a target that can change.
 */
function useVisibleRows(total: number): { ref: RefObject<HTMLDivElement>; start: number; end: number } {
  const ref = useRef<HTMLDivElement>(null)
  const [range, setRange] = useState(() => ({ start: 0, end: Math.min(total, INITIAL_ROWS) }))
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = (): void => {
      const node = ref.current
      if (!node) return
      const box = node.getBoundingClientRect()
      const scroller = scrollParent(node)
      let top = 0
      let viewport = window.innerHeight
      if (scroller === node) {
        top = node.scrollTop
        viewport = node.clientHeight
      } else if (scroller instanceof HTMLElement) {
        const r = scroller.getBoundingClientRect()
        top = Math.max(0, r.top - box.top)
        viewport = r.height
      } else {
        top = Math.max(0, -box.top)
      }
      const w = rowWindow(total, top, viewport)
      setRange((p) => (p.start === w.start && p.end === w.end ? p : { start: w.start, end: w.end }))
    }
    measure()
    window.addEventListener('scroll', measure, true)
    window.addEventListener('resize', measure)
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => {
      window.removeEventListener('scroll', measure, true)
      window.removeEventListener('resize', measure)
      ro.disconnect()
    }
  }, [total])
  return { ref, ...range }
}

const ago = (ts: number): string => {
  const s = Date.now() / 1000 - ts
  if (s < 90) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return new Date(ts * 1000).toLocaleDateString()
}

/**
 * One revision rendered as a diff.
 *
 * A pending revision is shown against `current` — the doc as it stands right now — not against the
 * body the model saw. If the user typed in between, that is the only comparison that tells the truth
 * about what accepting would do, and `stale` says so out loud.
 */
export default function DiffView({
  revision, current, onAccept, onReject, onRestore, defaultMode = 'unified', collapsed = true
}: {
  revision: DocRevision
  current?: string
  onAccept?: () => void
  onReject?: () => void
  onRestore?: () => void
  defaultMode?: 'unified' | 'split'
  collapsed?: boolean
}): JSX.Element {
  const [mode, setMode] = useState<'unified' | 'split'>(defaultMode)
  const [whole, setWhole] = useState(!collapsed)
  const [copied, setCopied] = useState(false)
  const pending = revision.status === 'pending'
  const before = pending && current !== undefined ? current : revision.before

  const lines = useMemo(() => diffLines(before, revision.after), [before, revision.after])
  const stat = useMemo(() => diffStat(lines), [lines])
  const shown = useMemo(() => (whole ? [{ oldStart: 1, newStart: 1, lines }] : hunks(lines, 3)), [lines, whole])

  // Every row of every hunk, flattened once, so the window can slice across hunk boundaries.
  const rows = useMemo(() => {
    const out: Row[] = []
    let i = 0
    shown.forEach((h: Hunk, hi: number) => {
      if (!whole) out.push({ i: i++, hunk: hi, kind: 'head', newStart: h.newStart })
      if (mode === 'unified') for (const line of h.lines) out.push({ i: i++, hunk: hi, kind: 'line', line })
      else for (const r of pairLines(h.lines)) out.push({ i: i++, hunk: hi, kind: 'split', left: r.left, right: r.right })
    })
    return out
  }, [shown, whole, mode])

  const { ref: bodyRef, start, end } = useVisibleRows(rows.length)
  const visible = rows.slice(start, end)
  // Rebuild the hunk wrappers around whichever rows are on screen; a hunk's rows are contiguous.
  const groups = useMemo(() => {
    const out: { hunk: number; rows: Row[] }[] = []
    for (const r of visible) {
      const last = out[out.length - 1]
      if (last && last.hunk === r.hunk) last.rows.push(r)
      else out.push({ hunk: r.hunk, rows: [r] })
    }
    return out
  }, [visible])

  const copy = (): void => {
    void navigator.clipboard.writeText(toUnified(before, revision.after))
    setCopied(true)
    setTimeout(() => setCopied(false), 1200)
  }

  const who = revision.author === 'assistant' ? (revision.tool === 'recording_summary' ? 'Recording summary' : 'Assistant') : 'You'

  return (
    <div className={`diff-card ${pending ? 'pending' : ''}`}>
      <header>
        <span className={`diff-who ${revision.author}`} title={revision.author === 'assistant' ? `Proposed by the assistant${revision.tool ? ` via ${revision.tool}` : ''}` : 'Your edit'}>
          {revision.author === 'assistant' ? <Sparkles size={12} /> : <User size={12} />}
          {who}
        </span>
        {/* A summary that only repeats the author label adds nothing; say it once. */}
        {revision.summary !== who && <span className="diff-summary" title={revision.summary}>{revision.summary || 'Edit'}</span>}
        <span className="diff-stat">
          {stat.added > 0 && <span className="plus">+{stat.added}</span>}
          {stat.removed > 0 && <span className="minus">−{stat.removed}</span>}
          {stat.added === 0 && stat.removed === 0 && <span className="muted small">no change</span>}
        </span>
        <span className="muted small">{ago(revision.created_at)}</span>
        <span className="spacer" />
        <button className="icon-btn ghost" title={mode === 'unified' ? 'Side by side' : 'Unified'} onClick={() => setMode(mode === 'unified' ? 'split' : 'unified')}>
          {mode === 'unified' ? <Columns2 size={13} /> : <AlignLeft size={13} />}
        </button>
        <button className="icon-btn ghost" title="Copy as a unified patch" onClick={copy}>
          {copied ? <Check size={13} /> : <Copy size={13} />}
        </button>
        {onRestore && !pending && (
          <button className="icon-btn ghost" title="Restore the file to this version" onClick={onRestore}><RotateCcw size={13} /></button>
        )}
      </header>

      {revision.stale && (
        <p className="diff-stale">
          <AlertTriangle size={12} />
          You edited this file after the assistant proposed the change, so the diff below is against your current text.
          Accepting replaces it with the proposal.
        </p>
      )}
      {revision.title_after && revision.title_after !== revision.title_before && (
        <p className="diff-retitle">Title → <strong>{revision.title_after}</strong></p>
      )}

      <div className={`diff-body ${mode}`} ref={bodyRef}>
        {shown.length === 0 ? (
          <p className="muted small pad">Nothing changed.</p>
        ) : (
          <>
            {start > 0 && <div className="diff-pad" style={{ height: start * DIFF_ROW_HEIGHT }} />}
            {groups.map((g) => (
              <div key={g.hunk} className="diff-hunk">
                {g.rows.map((r) => <DiffRow key={r.i} row={r} />)}
              </div>
            ))}
            {end < rows.length && <div className="diff-pad" style={{ height: (rows.length - end) * DIFF_ROW_HEIGHT }} />}
          </>
        )}
      </div>

      {/* The decision leads, primary first, as on every other card; the view toggle trails. */}
      <footer>
        {pending && onAccept && <button className="primary-btn sm" onClick={onAccept}>Accept</button>}
        {pending && onReject && <button className="ghost-btn sm" onClick={onReject}>Reject</button>}
        <span className="spacer" />
        {lines.some((l) => l.op === 'same') && (
          <button className="link small" onClick={() => setWhole((w) => !w)}>
            {whole ? 'Show changes only' : 'Show the whole file'}
          </button>
        )}
      </footer>
    </div>
  )
}
