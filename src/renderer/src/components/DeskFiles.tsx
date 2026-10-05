import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronRight, Columns2, FileImage, FilePlus, FileText, Folder, PackageCheck, RefreshCw } from 'lucide-react'
import { DESK_LIVE, type DeskDiff, type DeskFile, type DeskRichPreview, type FullDesk } from '@shared/types'
import { api } from '../lib/api'
import { wordDiff, type Op, type WordPart } from '../lib/diff'
import { deliveredPaths, deliveryLabel, fileKind, fmtAgo, fmtBytes } from '../lib/deskFiles'
import { useStore } from '../store'
import MarkdownPreview from './MarkdownPreview'
import { Parts } from './DiffView'

/**
 * The workspace, as the user sees it: `outputs/` first because that is what the review is about,
 * `work/` collapsed because it is the agent's scratch. Every file carries its state against the
 * `.baseline/` snapshot, so "what did it actually change" is answerable without reading anything.
 */

/** While the desk works its files change under the user's eyes; this is how often the tree is re-read. Idle desks are never polled. */
const LIVE_REFRESH_MS = 5000

/** The top-level folder a path belongs to, which is the only grouping the tree needs. */
const topOf = (p: string): string => (p.includes('/') ? p.split('/')[0] : '')

const GROUP_ORDER = ['outputs', 'inputs', '', 'work']
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

interface Loaded {
  key: string
  first: DeskRichPreview
  /** Text pages appended by "Load more", in order; the first page is `first`. */
  more: string[]
  next: number | null
  loading: boolean
}

/**
 * The rich preview of one file, paged. `stamp` changes when the file does (size + mtime from the
 * tree), which refetches in place: the pages already loaded are fetched again so a live refresh
 * does not collapse a long file back to its first page, and the old content stays on screen
 * until the new arrives, so the pane and its scroll position do not flash.
 */
function useRichPreview(deskId: string, path: string | null, stamp: string): { data: Loaded | null; more: () => void; error: string | null } {
  const [data, setData] = useState<Loaded | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pages = useRef(1)
  const key = `${deskId}:${path}`

  useEffect(() => { pages.current = 1; setData(null); setError(null) }, [key])
  useEffect(() => {
    if (!path) return
    let gone = false
    void (async () => {
      try {
        const first = await api.cowork.desks.preview(deskId, path, 0)
        if (first.kind !== 'text' && first.kind !== 'document') { if (!gone) setData({ key, first, more: [], next: null, loading: false }); return }
        const more: string[] = []
        let next = first.next_offset
        for (let i = 1; i < pages.current && next !== null; i++) {
          const r = await api.cowork.desks.preview(deskId, path, next)
          if (r.kind !== 'text' && r.kind !== 'document') break
          more.push(r.text)
          next = r.next_offset
        }
        if (!gone) { setData({ key, first, more, next, loading: false }); setError(null) }
      } catch (e) {
        if (!gone) setError((e as Error).message)
      }
    })()
    return () => { gone = true }
  }, [deskId, path, stamp, key])

  const more = (): void => {
    if (!path || !data || data.next === null || data.loading) return
    const at = data.next
    setData({ ...data, loading: true })
    api.cowork.desks.preview(deskId, path, at).then((r) => {
      if (r.kind !== 'text' && r.kind !== 'document') return
      pages.current += 1
      setData((d) => (d && d.key === key ? { ...d, more: [...d.more, r.text], next: r.next_offset, loading: false } : d))
    }).catch((e: Error) => { setError(e.message); setData((d) => (d ? { ...d, loading: false } : d)) })
  }
  return { data: data && data.key === key ? data : null, more, error }
}

function PreviewBody({ path, data, onMore, error }: { path: string; data: Loaded | null; onMore: () => void; error: string | null }): JSX.Element {
  if (error && !data) return <p className="muted small">Could not load this file: {error}</p>
  if (!data) return <p className="muted small">Loading…</p>
  const p = data.first
  if (p.kind === 'none') return <p className="muted small">{p.reason}</p>
  if (p.kind === 'image') {
    return (
      <div className="desk-preview-image">
        <img src={p.data_url} alt={path} />
        <p className="muted small">{p.width} × {p.height} preview</p>
      </div>
    )
  }
  const text = [p.text, ...data.more].join('')
  return (
    <>
      {p.kind === 'document' && <p className="muted small desk-preview-note">{p.note}</p>}
      {fileKind(path, true) === 'markdown' && p.kind === 'text'
        ? <div className="markdown desk-preview-md"><MarkdownPreview source={text} /></div>
        : <pre className="desk-preview-text">{text}</pre>}
      {data.next !== null && (
        <div className="desk-preview-more">
          <button className="ghost-btn" disabled={data.loading} onClick={onMore}>{data.loading ? 'Loading…' : 'Load more'}</button>
          <span className="muted small">{text.length.toLocaleString()} of {p.total_chars.toLocaleString()} characters</span>
        </div>
      )}
    </>
  )
}

export default function DeskFiles({ desk }: { desk: FullDesk }): JSX.Element {
  const files = useStore((s) => s.deskFiles)
  const loadDeskFiles = useStore((s) => s.loadDeskFiles)
  const [path, setPath] = useState<string | null>(null)
  const toast = useStore((s) => s.toast)
  const [shut, setShut] = useState<Record<string, boolean>>({ work: true })
  const [showDiff, setShowDiff] = useState(false)
  const [diff, setDiff] = useState<DeskDiff | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => { void loadDeskFiles(desk.id); setPath(null) }, [desk.id, loadDeskFiles])

  // Live: re-read the tree on every status change and every few seconds while the desk is working.
  // A desk at rest is never polled; `loadDeskFiles` replaces the list in place so the selection
  // and the scroll position of both panes survive.
  const live = DESK_LIVE.includes(desk.status)
  useEffect(() => { void loadDeskFiles(desk.id, '', true) }, [desk.status, desk.id, loadDeskFiles])
  useEffect(() => {
    if (!live) return
    const t = setInterval(() => { void loadDeskFiles(desk.id, '', true) }, LIVE_REFRESH_MS)
    return () => clearInterval(t)
  }, [live, desk.id, loadDeskFiles])
  const delivered = useMemo(() => deliveredPaths(desk.outputs), [desk.outputs])

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

  const current = files.find((f) => f.path === path) ?? null
  const { data: rich, more: loadMore, error: richError } = useRichPreview(desk.id, path, current ? `${current.bytes}:${current.modified}` : '')

  // The diff is per file and fetched on demand, so switching files drops the one on screen rather
  // than showing the previous file's changes under the new file's name.
  const stamp = current ? `${current.bytes}:${current.modified}` : ''
  useEffect(() => { setDiff(null) }, [path, stamp])
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

  const open = (p: string): void => { setShowDiff(false); setPath(p) }
  // Snapshot copies into the read-only inputs/ folder; the desk's next turn is told about them.
  const addInputs = async (): Promise<void> => {
    const paths = await window.os.data.chooseInputFiles()
    if (!paths.length) return
    try {
      const r = await api.cowork.desks.addInputs(desk.id, paths.map((path) => ({ kind: 'path' as const, path })))
      toast(`Added ${r.added.map((a) => a.path).join(', ')}`)
      void loadDeskFiles(desk.id, '', true)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  return (
    <div className="desk-files">
      <div className="desk-tree">
        <div className="desk-tree-head">
          <span className="muted small">{files.filter((f) => !f.is_dir).length} files</span>
          <span className="spacer" />
          <button className="icon-btn ghost xs" title="Add input files (copied into the read-only inputs/ folder)" onClick={() => void addInputs()}><FilePlus size={12} /></button>
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
                {fileKind(f.path, f.is_text) === 'image' ? <FileImage size={12} className="desk-file-icon" /> : <FileText size={12} className="desk-file-icon" />}
                <span className="desk-file-name">{g ? f.path.slice(g.length + 1) : f.path}</span>
                {g === 'outputs' && delivered.has(f.path) && (
                  <span className="desk-file-delivered" title="Delivered with desk_deliver"><PackageCheck size={11} />{deliveryLabel(delivered.get(f.path))}</span>
                )}
                {f.state !== 'unchanged' && <span className={`desk-file-state ${f.state}`}>{f.state}</span>}
                <span className="desk-file-size">{fmtBytes(f.bytes)}</span>
              </div>
            ))}
          </section>
        ))}
      </div>

      <div className="desk-preview">
        {!path ? (
          <p className="empty-hint">Pick a file to read it.</p>
        ) : (
          <>
            <div className="desk-preview-head">
              <b>{path}</b>
              {current && current.state !== 'unchanged' && <span className={`desk-file-state ${current.state}`}>{current.state}</span>}
              {current && <span className="muted small desk-preview-meta">{fmtBytes(current.bytes)} · modified {fmtAgo(current.modified)}</span>}
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
            ) : (
              <PreviewBody path={path} data={rich} onMore={loadMore} error={richError} />
            )}
          </>
        )}
      </div>
    </div>
  )
}
