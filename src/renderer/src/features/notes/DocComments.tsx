import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Check, MessageSquarePlus, RotateCcw, Trash2, Unlink } from 'lucide-react'
import type { DocComment } from '@shared/types'
import { api } from '../../lib/api'
import { buildThreads, makeAnchor, type Anchor, type Span, type Thread } from './comments'

/**
 * Comments on a doc: threads anchored to a span of the *rendered* text. The rendered pane is the text the
 * anchors are made from and found in again (comments.ts), so a quote is what the reader saw, not markdown.
 * Marks are painted with the CSS highlight registry, which colours ranges without touching the DOM that the
 * markdown renderer owns. A thread the text no longer contains is listed as detached.
 */

const HL = 'doc-comment'
const HL_ACTIVE = 'doc-comment-active'
const POLL_MS = 15_000  // a reply the page agent posts shows up without a reload

type HighlightCtor = new (...ranges: Range[]) => object
const highlightApi = (): { H: HighlightCtor; reg: Map<string, object> } | null => {
  const H = (globalThis as { Highlight?: HighlightCtor }).Highlight
  const reg = (CSS as unknown as { highlights?: Map<string, object> }).highlights
  return H && reg ? { H, reg } : null
}

/** Character offset of a DOM point within `root`, counted the way the anchors count (text nodes in order). */
function offsetIn(root: HTMLElement, node: Node, offset: number): number {
  const r = document.createRange()
  r.setStart(root, 0)
  r.setEnd(node, offset)
  return r.toString().length
}

/** The DOM range for a span of `root`'s text, or null when the text is shorter than the span. */
function rangeFor(root: HTMLElement, span: Span): Range | null {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const r = document.createRange()
  let pos = 0
  let started = false
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const len = (n as Text).data.length
    if (!started && span.start <= pos + len) { r.setStart(n, span.start - pos); started = true }
    if (started && span.end <= pos + len) { r.setEnd(n, span.end - pos); return r }
    pos += len
  }
  return null
}

const sameThreads = (a: Thread[], b: Thread[]): boolean =>
  a.length === b.length && a.every((t, i) => t.root === b[i].root && t.replies.length === b[i].replies.length &&
    t.span?.start === b[i].span?.start && t.span?.end === b[i].span?.end)

export interface SelectionFab { x: number; y: number; anchor: Anchor }

export interface DocCommentsState {
  threads: Thread[]
  openCount: number
  active: string | null
  setActive: (id: string | null) => void
  /** A selection in the rendered pane waiting for its Comment button to be pressed. */
  fab: SelectionFab | null
  clearFab: () => void
  /** The anchor a new thread is being written for. */
  draft: Anchor | null
  startDraft: (a: Anchor) => void
  cancelDraft: () => void
  add: (body: string) => Promise<void>
  reply: (threadId: string, body: string) => Promise<void>
  edit: (id: string, body: string) => Promise<void>
  resolve: (id: string, resolved: boolean) => Promise<void>
  remove: (id: string) => Promise<void>
  /** Scroll the rendered pane to a thread's passage. */
  jump: (t: Thread) => void
  reload: () => Promise<void>
}

/**
 * @param pane  the rendered pane, when it is on screen; the marks and the selection button live there
 * @param body  the markdown, which the anchors fall back to while the rendered pane is not mounted
 * @param paneKey  changes whenever the pane mounts or unmounts (the ref itself never does), so the marks follow it
 * @param onActivate  called when a mark is clicked, so the host can open the panel on the thread
 */
export function useDocComments(docId: string, body: string, pane: React.RefObject<HTMLDivElement>, paneKey: string, live: boolean,
  onActivate: (threadId: string) => void): DocCommentsState {
  const [rows, setRows] = useState<DocComment[]>([])
  const [threads, setThreads] = useState<Thread[]>([])
  const [active, setActive] = useState<string | null>(null)
  const [fab, setFab] = useState<SelectionFab | null>(null)
  const [draft, setDraft] = useState<Anchor | null>(null)
  const threadsRef = useRef(threads)
  threadsRef.current = threads
  const activateRef = useRef(onActivate)
  activateRef.current = onActivate

  const reload = useCallback(async (): Promise<void> => {
    if (!docId) return setRows([])
    try { setRows(await api.docs.comments(docId)) } catch { /* the panel keeps the last list */ }
  }, [docId])
  useEffect(() => { setActive(null); setDraft(null); setFab(null); void reload() }, [reload])
  useEffect(() => setFab(null), [paneKey])
  useEffect(() => {
    if (!live) return
    const t = setInterval(() => void reload(), POLL_MS)
    return () => clearInterval(t)
  }, [live, reload])

  // Locate every thread against the text as rendered, after each render of the pane; the markdown
  // itself is the fallback so the list still shows in editor-only mode.
  useLayoutEffect(() => {
    const text = pane.current?.textContent ?? body
    // Keep the old array when nothing moved: this runs per keystroke in the split view, and a new
    // reference would re-render the whole Files view each time.
    setThreads((old) => {
      const next = buildThreads(rows, text)
      return sameThreads(old, next) ? old : next
    })
  }, [rows, body, pane, paneKey])

  // Paint the marks. The registry colours ranges in place: no wrapper elements, nothing the renderer
  // has to know about. Both run again when the pane re-renders, since its text nodes are new.
  useLayoutEffect(() => {
    const api_ = highlightApi()
    const root = pane.current
    if (!api_) return
    if (!root) { api_.reg.delete(HL); api_.reg.delete(HL_ACTIVE); return }
    const open = threads.filter((t) => t.span && !t.root.resolved)
    const ranges = open.map((t) => rangeFor(root, t.span!)).filter(Boolean) as Range[]
    api_.reg.set(HL, new api_.H(...ranges))
    const cur = threads.find((t) => t.root.id === active)
    const curRange = cur?.span ? rangeFor(root, cur.span) : null
    if (curRange) api_.reg.set(HL_ACTIVE, new api_.H(curRange))
    else api_.reg.delete(HL_ACTIVE)
    return () => { api_.reg.delete(HL); api_.reg.delete(HL_ACTIVE) }
  }, [threads, active, pane, body, paneKey])

  // A selection in the pane offers Comment; a click on a mark opens its thread.
  useEffect(() => {
    const root = pane.current
    if (!root) return
    const up = (): void => {
      setTimeout(() => {
        const sel = window.getSelection()
        if (!sel || sel.isCollapsed || sel.rangeCount === 0) return setFab(null)
        const r = sel.getRangeAt(0)
        if (!root.contains(r.startContainer) || !root.contains(r.endContainer)) return setFab(null)
        const text = root.textContent ?? ''
        const start = offsetIn(root, r.startContainer, r.startOffset)
        const end = offsetIn(root, r.endContainer, r.endOffset)
        if (end <= start || !text.slice(start, end).trim()) return setFab(null)
        const rect = r.getBoundingClientRect()
        setFab({ x: rect.left + rect.width / 2, y: Math.max(rect.top - 6, 44), anchor: makeAnchor(text, start, end) })
      }, 0)
    }
    const click = (e: MouseEvent): void => {
      const sel = window.getSelection()
      if (sel && !sel.isCollapsed) return
      const doc = document as Document & {
        caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null
        caretRangeFromPoint?: (x: number, y: number) => Range | null
      }
      const pos = doc.caretPositionFromPoint?.(e.clientX, e.clientY)
      const old = pos ? null : doc.caretRangeFromPoint?.(e.clientX, e.clientY)
      const point = pos ? { node: pos.offsetNode, offset: pos.offset } : old ? { node: old.startContainer, offset: old.startOffset } : null
      if (!point || !root.contains(point.node)) return
      const at = offsetIn(root, point.node, point.offset)
      const hit = threadsRef.current.find((t) => t.span && !t.root.resolved && t.span.start <= at && at < t.span.end)
      if (hit) { setActive(hit.root.id); activateRef.current(hit.root.id) }
    }
    root.addEventListener('mouseup', up)
    root.addEventListener('click', click)
    return () => { root.removeEventListener('mouseup', up); root.removeEventListener('click', click) }
  }, [pane, docId, paneKey])
  useEffect(() => {
    if (!fab) return
    const hide = (e: Event): void => { if (!(e.target instanceof Element && e.target.closest('.doc-comment-fab'))) setFab(null) }
    document.addEventListener('mousedown', hide)
    document.addEventListener('scroll', hide, true)
    return () => { document.removeEventListener('mousedown', hide); document.removeEventListener('scroll', hide, true) }
  }, [fab])

  const jump = useCallback((t: Thread): void => {
    const root = pane.current
    if (!root || !t.span) return
    const r = rangeFor(root, t.span)
    if (!r) return
    const rect = r.getBoundingClientRect()
    const box = root.getBoundingClientRect()
    root.scrollTo({ top: root.scrollTop + rect.top - box.top - root.clientHeight / 3, behavior: 'smooth' })
  }, [pane])

  const after = async (p: Promise<unknown>): Promise<void> => { try { await p } finally { await reload() } }
  return {
    threads,
    openCount: threads.filter((t) => !t.root.resolved).length,
    active, setActive,
    fab, clearFab: () => setFab(null),
    draft,
    startDraft: (a) => { setDraft(a); setFab(null); window.getSelection()?.removeAllRanges() },
    cancelDraft: () => setDraft(null),
    add: async (text) => {
      if (!draft) return
      const made = await api.docs.addComment(docId, { body: text, ...draft })
      setDraft(null)
      setActive(made.id)
      await reload()
    },
    reply: (threadId, text) => after(api.docs.replyComment(threadId, text)),
    edit: (id, text) => after(api.docs.patchComment(id, { body: text })),
    resolve: (id, resolved) => after(api.docs.patchComment(id, { resolved })),
    remove: (id) => after(api.docs.deleteComment(id)),
    jump,
    reload
  }
}

/** The floating Comment button over a selection in the rendered pane. */
export function DocCommentFab({ fab, onComment }: { fab: SelectionFab | null; onComment: () => void }): JSX.Element | null {
  if (!fab) return null
  return (
    <div className="selection-toolbar doc-comment-fab" style={{ left: Math.min(Math.max(fab.x, 80), window.innerWidth - 80), top: fab.y }}
      onMouseDown={(e) => e.preventDefault() /* keep the selection alive while clicking */}>
      <button type="button" onClick={onComment}><MessageSquarePlus size={13} /> Comment</button>
    </div>
  )
}

const when = (t: number): string => {
  const d = new Date(t * 1000)
  const sameDay = d.toDateString() === new Date().toDateString()
  return sameDay ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

function Composer({ placeholder, submit, cancel, initial = '', autoFocus }: {
  placeholder: string; submit: (text: string) => void | Promise<void>; cancel?: () => void; initial?: string; autoFocus?: boolean
}): JSX.Element {
  const [text, setText] = useState(initial)
  const [busy, setBusy] = useState(false)
  const send = async (): Promise<void> => {
    const t = text.trim()
    if (!t || busy) return
    setBusy(true)
    try { await submit(t); setText('') } finally { setBusy(false) }
  }
  return (
    <div className="doc-comment-compose">
      <textarea value={text} placeholder={placeholder} rows={2} autoFocus={autoFocus} aria-label={placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void send() }
          if (e.key === 'Escape' && cancel) cancel()
        }} />
      <div className="doc-thread-actions">
        <button type="button" className="primary-btn small" disabled={!text.trim() || busy} onClick={() => void send()}>{initial ? 'Save' : 'Post'}</button>
        {cancel && <button type="button" className="ghost-btn xs" onClick={cancel}>Cancel</button>}
        <span className="muted small">⌘↩</span>
      </div>
    </div>
  )
}

function CommentRow({ c, state, canEdit }: { c: DocComment; state: DocCommentsState; canEdit: boolean }): JSX.Element {
  const [editing, setEditing] = useState(false)
  return (
    <div className={`doc-comment ${c.author}`}>
      <div className="doc-comment-head">
        <span className={`doc-comment-who ${c.author}`}>{c.author === 'agent' ? 'Assistant' : 'You'}</span>
        <span className="muted small">{when(c.created_at)}{c.updated_at - c.created_at > 1 ? ' · edited' : ''}</span>
        <span className="spacer" />
        {canEdit && !editing && <button type="button" className="link xs" onClick={() => setEditing(true)}>Edit</button>}
        <button type="button" className="icon-btn ghost xs" title={c.parent_id ? 'Delete reply' : 'Delete thread'} aria-label="Delete"
          onClick={() => { if (confirm(c.parent_id ? 'Delete this reply?' : 'Delete this thread and its replies?')) void state.remove(c.id) }}><Trash2 size={12} /></button>
      </div>
      {editing
        ? <Composer placeholder="Edit comment" initial={c.body} autoFocus cancel={() => setEditing(false)} submit={async (t) => { await state.edit(c.id, t); setEditing(false) }} />
        : <p className="doc-comment-body">{c.body}</p>}
    </div>
  )
}

function ThreadCard({ t, state }: { t: Thread; state: DocCommentsState }): JSX.Element {
  const resolved = !!t.root.resolved
  const isActive = state.active === t.root.id
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => { if (isActive) ref.current?.scrollIntoView({ block: 'nearest' }) }, [isActive])
  return (
    <div ref={ref} className={`doc-thread ${isActive ? 'active' : ''} ${resolved ? 'resolved' : ''}`} onClick={() => state.setActive(t.root.id)}>
      <button type="button" className="doc-thread-quote" title={t.span ? 'Show in the text' : 'The text this was about is no longer in the file'}
        onClick={(e) => { e.stopPropagation(); state.setActive(t.root.id); state.jump(t) }}>
        {t.span === null && <Unlink size={11} className="doc-thread-detached" aria-label="Detached" />}
        <span>{t.root.quote || '(no passage)'}</span>
      </button>
      <CommentRow c={t.root} state={state} canEdit={t.root.author === 'user'} />
      {t.replies.map((r) => <CommentRow key={r.id} c={r} state={state} canEdit={r.author === 'user'} />)}
      {isActive && !resolved && <Composer placeholder="Reply" submit={(text) => state.reply(t.root.id, text)} />}
      <div className="doc-thread-actions">
        {resolved
          ? <button type="button" className="ghost-btn xs" onClick={(e) => { e.stopPropagation(); void state.resolve(t.root.id, false) }}><RotateCcw size={12} /> Reopen</button>
          : <button type="button" className="ghost-btn xs" onClick={(e) => { e.stopPropagation(); void state.resolve(t.root.id, true) }}><Check size={12} /> Resolve</button>}
      </div>
    </div>
  )
}

/** The Comments tab of the side panel: the draft composer, then threads in document order. */
export function CommentsPanel({ state }: { state: DocCommentsState }): JSX.Element {
  const [showResolved, setShowResolved] = useState(false)
  const shown = state.threads.filter((t) => showResolved || !t.root.resolved)
  const resolvedCount = state.threads.length - state.openCount
  return (
    <div className="doc-comments">
      {state.draft && (
        <div className="doc-thread active draft">
          <div className="doc-thread-quote"><span>{state.draft.quote}</span></div>
          <Composer placeholder="Comment" autoFocus cancel={state.cancelDraft} submit={state.add} />
        </div>
      )}
      {shown.map((t) => <ThreadCard key={t.root.id} t={t} state={state} />)}
      {shown.length === 0 && !state.draft && (
        <p className="empty-hint">Select some text in the reading view and press Comment.</p>
      )}
      {resolvedCount > 0 && (
        <label className="doc-comments-foot">
          <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} /> Show {resolvedCount} resolved
        </label>
      )}
    </div>
  )
}
