import { useMemo, useState } from 'react'
import { Check, X, Columns2, AlignLeft, Copy, RotateCcw, Sparkles, User, AlertTriangle } from 'lucide-react'
import type { DocRevision } from '@shared/types'
import { diffLines, diffStat, hunks, toUnified, type DiffLine, type WordPart } from '../lib/diff'

/** Word parts of a reworded line, so only the words that actually moved are marked. */
function Parts({ parts, text, op }: { parts: WordPart[] | undefined; text: string; op: DiffLine['op'] }): JSX.Element {
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

function UnifiedRows({ lines }: { lines: DiffLine[] }): JSX.Element {
  return (
    <>
      {lines.map((l, i) => (
        <div key={i} className={`diff-row ${l.op}`}>
          <span className="diff-no">{l.oldNo ?? ''}</span>
          <span className="diff-no">{l.newNo ?? ''}</span>
          <span className="diff-sign">{SIGN[l.op]}</span>
          <span className="diff-text"><Parts parts={l.parts} text={l.text} op={l.op} /></span>
        </div>
      ))}
    </>
  )
}

/**
 * Split view pairs each deletion with the addition that replaced it, so a rewording sits on one row
 * with the old wording on the left and the new on the right.
 */
function SplitRows({ lines }: { lines: DiffLine[] }): JSX.Element {
  const rows = useMemo(() => {
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
  }, [lines])

  const cell = (l: DiffLine | null, side: 'left' | 'right'): JSX.Element => (
    <>
      <span className="diff-no">{(side === 'left' ? l?.oldNo : l?.newNo) ?? ''}</span>
      <span className={`diff-text ${l ? (l.op === 'same' ? 'same' : side === 'left' ? 'del' : 'add') : 'void'}`}>
        {l ? <Parts parts={l.parts} text={l.text} op={side === 'left' ? 'del' : 'add'} /> : ''}
      </span>
    </>
  )

  return (
    <>
      {rows.map((r, i) => (
        <div key={i} className="diff-split-row">
          {cell(r.left, 'left')}
          {cell(r.right, 'right')}
        </div>
      ))}
    </>
  )
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

  const copy = (): void => {
    void navigator.clipboard.writeText(toUnified(before, revision.after))
    setCopied(true)
    setTimeout(() => setCopied(false), 1200)
  }

  return (
    <div className={`diff-card ${pending ? 'pending' : ''}`}>
      <header>
        <span className={`diff-who ${revision.author}`} title={revision.author === 'assistant' ? `Proposed by the assistant${revision.tool ? ` via ${revision.tool}` : ''}` : 'Your edit'}>
          {revision.author === 'assistant' ? <Sparkles size={12} /> : <User size={12} />}
          {revision.author === 'assistant' ? 'Assistant' : 'You'}
        </span>
        <span className="diff-summary" title={revision.summary}>{revision.summary || 'Edit'}</span>
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
          <button className="icon-btn ghost" title="Restore the document to this version" onClick={onRestore}><RotateCcw size={13} /></button>
        )}
      </header>

      {revision.stale && (
        <p className="diff-stale">
          <AlertTriangle size={12} />
          You edited this doc after the assistant proposed the change, so the diff below is against your current text.
          Accepting replaces it with the proposal.
        </p>
      )}
      {revision.title_after && revision.title_after !== revision.title_before && (
        <p className="diff-retitle">Title → <strong>{revision.title_after}</strong></p>
      )}

      <div className={`diff-body ${mode}`}>
        {shown.length === 0 ? (
          <p className="muted small pad">Nothing changed.</p>
        ) : (
          shown.map((h, i) => (
            <div key={i} className="diff-hunk">
              {!whole && shown.length > 0 && <div className="diff-hunk-head">@@ line {h.newStart} @@</div>}
              {mode === 'unified' ? <UnifiedRows lines={h.lines} /> : <SplitRows lines={h.lines} />}
            </div>
          ))
        )}
      </div>

      <footer>
        {lines.some((l) => l.op === 'same') && (
          <button className="link small" onClick={() => setWhole((w) => !w)}>
            {whole ? 'Show changes only' : 'Show the whole document'}
          </button>
        )}
        <span className="spacer" />
        {pending && onReject && <button className="ghost-btn" onClick={onReject}><X size={13} /> Reject</button>}
        {pending && onAccept && <button className="primary-btn" onClick={onAccept}><Check size={13} /> Accept</button>}
      </footer>
    </div>
  )
}
