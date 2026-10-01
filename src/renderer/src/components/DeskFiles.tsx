import { useEffect, useMemo, useState } from 'react'
import { ChevronRight, Columns2, FileText, Folder, RefreshCw } from 'lucide-react'
import type { DeskDiff, DeskFile, FullDesk } from '@shared/types'
import { api } from '../lib/api'
import { wordDiff, type Op, type WordPart } from '../lib/diff'
import { useStore } from '../store'
import MarkdownPreview from './MarkdownPreview'

/**
 * The workspace, as the user sees it: `outputs/` first because that is what the review is about,
 * `work/` collapsed because it is the agent's scratch. Every file carries its state against the
 * `.baseline/` snapshot, so "what did it actually change" is answerable without reading anything.
 */

const fmtBytes = (n: number): string =>
  n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`

const isMarkdown = (p: string): boolean => /\.(md|markdown)$/i.test(p)
const isImage = (p: string): boolean => /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/i.test(p)

/** The top-level folder a path belongs to, which is the only grouping the tree needs. */
const topOf = (p: string): string => (p.includes('/') ? p.split('/')[0] : '')

const GROUP_ORDER = ['outputs', '', 'work']
const groupRank = (g: string): number => {
  const i = GROUP_ORDER.indexOf(g)
  return i < 0 ? GROUP_ORDER.length : i
}

interface DiffRow {
  op: Op
  text: string
  parts?: WordPart[]
  hunk?: string
}

/**
 * The backend hands back a unified diff as text (stdlib `difflib.unified_diff`), so this reads it
 * rather than recomputing one: `lib/diff.ts` builds diffs from two documents and has no reader. Its
 * `wordDiff` still does the useful half — a lone `-`/`+` pair is a reworded line, and marking just
 * the words that moved is the difference between a readable diff and a wall of red.
 */
function parseUnified(diff: string): DiffRow[] {
  const rows: DiffRow[] = []
  for (const raw of diff.split('\n')) {
    if (raw.startsWith('--- ') || raw.startsWith('+++ ')) continue
    if (raw.startsWith('@@')) { rows.push({ op: 'same', text: '', hunk: raw }); continue }
    if (raw.startsWith('+')) rows.push({ op: 'add', text: raw.slice(1) })
    else if (raw.startsWith('-')) rows.push({ op: 'del', text: raw.slice(1) })
    else rows.push({ op: 'same', text: raw.startsWith(' ') ? raw.slice(1) : raw })
  }
  for (let i = 0; i < rows.length - 1; i++) {
    const a = rows[i]
    const b = rows[i + 1]
    const lone = a.op === 'del' && b.op === 'add' && (i === 0 || rows[i - 1].op !== 'del') && (i + 2 >= rows.length || rows[i + 2].op !== 'add')
    if (!lone) continue
    const w = wordDiff(a.text, b.text)
    a.parts = w.before
    b.parts = w.after
  }
  return rows
}

function Parts({ parts, text, op }: { parts: WordPart[] | undefined; text: string; op: Op }): JSX.Element {
  if (!parts) return <>{text}</>
  return (
    <>
      {parts.map((p, i) => (p.changed ? <mark key={i} className={op === 'add' ? 'w-add' : 'w-del'}>{p.text}</mark> : <span key={i}>{p.text}</span>))}
    </>
  )
}

function DiffPane({ diff }: { diff: DeskDiff }): JSX.Element {
  const rows = useMemo(() => parseUnified(diff.diff), [diff.diff])
  return (
    <div className="desk-diff">
      <p className="muted small">
        <span className="plus">+{diff.added}</span> <span className="minus">−{diff.removed}</span>
        {!diff.has_baseline && ' · the desk created this file, so it diffs against nothing'}
        {diff.truncated && ' · truncated'}
      </p>
      {rows.length === 0
        ? <p className="muted small">No change against the baseline.</p>
        : rows.map((r, i) => (
          r.hunk
            ? <div key={i} className="desk-diff-hunk">{r.hunk}</div>
            : <div key={i} className={`desk-diff-line ${r.op === 'add' ? 'diff-add' : r.op === 'del' ? 'diff-del' : ''}`}>
              <span className="desk-diff-sign">{r.op === 'add' ? '+' : r.op === 'del' ? '−' : ' '}</span>
              <span className="desk-diff-text"><Parts parts={r.parts} text={r.text} op={r.op} /></span>
            </div>
        ))}
    </div>
  )
}

export default function DeskFiles({ desk }: { desk: FullDesk }): JSX.Element {
  const files = useStore((s) => s.deskFiles)
  const preview = useStore((s) => s.deskPreview)
  const loadDeskFiles = useStore((s) => s.loadDeskFiles)
  const previewDeskFile = useStore((s) => s.previewDeskFile)
  const toast = useStore((s) => s.toast)
  const [shut, setShut] = useState<Record<string, boolean>>({ work: true })
  const [showDiff, setShowDiff] = useState(false)
  const [diff, setDiff] = useState<DeskDiff | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => { void loadDeskFiles(desk.id) }, [desk.id, loadDeskFiles])

  const groups = useMemo(() => {
    const m = new Map<string, DeskFile[]>()
    for (const f of files) {
      if (f.is_dir) continue
      const k = topOf(f.path)
      const arr = m.get(k)
      if (arr) arr.push(f)
      else m.set(k, [f])
    }
    return [...m.entries()].sort((a, b) => groupRank(a[0]) - groupRank(b[0]) || a[0].localeCompare(b[0]))
  }, [files])

  const path = preview?.path ?? null
  const current = files.find((f) => f.path === path) ?? null

  // The diff is per file and fetched on demand, so switching files drops the one on screen rather
  // than showing the previous file's changes under the new file's name.
  useEffect(() => { setDiff(null) }, [path])
  useEffect(() => {
    if (!showDiff || !path || diff) return
    let gone = false
    setBusy(true)
    api.cowork.desks.diff(desk.id, path)
      .then((d) => { if (!gone) setDiff(d) })
      .catch((e: Error) => { if (!gone) toast(e.message, 'error') })
      .finally(() => { if (!gone) setBusy(false) })
    return () => { gone = true }
  }, [showDiff, path, diff, desk.id, toast])

  const open = (p: string): void => { setShowDiff(false); void previewDeskFile(desk.id, p) }

  return (
    <div className="desk-files">
      <div className="desk-tree">
        <div className="desk-tree-head">
          <span className="muted small">{files.filter((f) => !f.is_dir).length} files</span>
          <span className="spacer" />
          <button className="icon-btn ghost xs" title="Reload" onClick={() => void loadDeskFiles(desk.id)}><RefreshCw size={12} /></button>
        </div>
        {files.length === 0 && <p className="empty-hint">Nothing in the workspace yet.</p>}
        {groups.map(([g, items]) => (
          <section key={g || '_root'}>
            {g && (
              <button className="desk-folder" onClick={() => setShut((x) => ({ ...x, [g]: !x[g] }))}>
                <ChevronRight size={11} className={shut[g] ? undefined : 'rot90'} />
                <Folder size={11} />{g}<span className="count">{items.length}</span>
              </button>
            )}
            {!shut[g] && items.map((f) => (
              <div
                key={f.path}
                className={`desk-file-row ${f.path === path ? 'active' : ''}`}
                role="button"
                tabIndex={0}
                onClick={() => open(f.path)}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(f.path) } }}
              >
                <FileText size={12} className="desk-file-icon" />
                <span className="desk-file-name">{g ? f.path.slice(g.length + 1) : f.path}</span>
                {f.state !== 'unchanged' && <span className={`desk-file-state ${f.state}`}>{f.state}</span>}
                <span className="desk-file-size">{fmtBytes(f.bytes)}</span>
              </div>
            ))}
          </section>
        ))}
      </div>

      <div className="desk-preview">
        {!path ? (
          <p className="empty-hint">Pick a file to read it. Changed files can be shown as a diff against what was there before the desk started.</p>
        ) : (
          <>
            <div className="desk-preview-head">
              <b>{path}</b>
              {current && current.state !== 'unchanged' && <span className={`desk-file-state ${current.state}`}>{current.state}</span>}
              <span className="spacer" />
              <button
                className={`ghost-btn ${showDiff ? 'on' : ''}`}
                aria-pressed={showDiff}
                title="Show the change against the pre-desk baseline"
                onClick={() => setShowDiff((d) => !d)}
              >
                <Columns2 size={13} /> Diff
              </button>
            </div>
            {showDiff ? (
              busy && !diff ? <p className="muted small">Reading the diff…</p> : diff ? <DiffPane diff={diff} /> : <p className="muted small">No diff available.</p>
            ) : current && !current.is_text ? (
              <p className="muted small">{isImage(path) ? 'An image. ' : ''}{fmtBytes(current.bytes)} of binary — nothing to show here.</p>
            ) : isMarkdown(path) ? (
              <div className="markdown desk-preview-md">{preview?.text ? <MarkdownPreview source={preview.text} /> : <p className="muted small">Loading…</p>}</div>
            ) : (
              <pre className="desk-preview-text">{preview?.text ?? ''}</pre>
            )}
          </>
        )}
      </div>
    </div>
  )
}
