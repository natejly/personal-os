import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ChevronDown, CloudUpload, Eye, FilePlus2, History, PanelLeftOpen, Pencil, PenLine, Search, Sparkles, Trash2, X } from 'lucide-react'
import type { Doc, DocMeta, DocVersion } from '@shared/types'
import { api } from '../lib/api'
import { useStore } from '../store'
import { SAFE_MD } from './Message'
import AgentBar from './AgentBar'

const SAVE_MS = 700

const FORMATS: { format: DocMeta['format']; label: string }[] = [
  { format: 'md', label: 'Markdown (.md)' },
  { format: 'txt', label: 'Plain text (.txt)' },
  { format: 'tex', label: 'LaTeX (.tex)' }
]

/** Unix-seconds timestamp → "5m ago"-style relative date, falling back to a plain date past a week. */
export const ago = (ts: number | null | undefined): string => {
  if (!ts) return ''
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  if (s < 7 * 86400) return `${Math.floor(s / 86400)}d ago`
  return new Date(ts * 1000).toLocaleDateString()
}

/** What the diff modal is showing: a unified diff plus the optional restore it offers. */
export interface DiffState {
  title: string
  text: string
  /** Version the action button restores; null hides the button (e.g. diffing current vs itself). */
  restoreTo: number | null
  /** The action button's label: 'Restore' from history, 'Revert' from an agent edit chip. */
  action: string
}

/** Unified diff, line by line: + green, - red, @@ muted. The body scrolls; header and footer stay. */
export function DiffModal({ state, onRestore, onClose }: {
  state: DiffState
  onRestore: (n: number) => void
  onClose: () => void
}): JSX.Element {
  const cls = (l: string): string =>
    l.startsWith('+++') || l.startsWith('---') ? 'file'
      : l.startsWith('@@') ? 'hunk'
        : l.startsWith('+') ? 'add'
          : l.startsWith('-') ? 'del' : 'ctx'
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal wide diff-modal" onMouseDown={(e) => e.stopPropagation()}>
        <header>
          <h2>{state.title}</h2>
          <button className="icon-btn" title="Close" onClick={onClose}><X size={16} /></button>
        </header>
        <div className="diff-view">
          {state.text.trim()
            ? state.text.split('\n').map((l, i) => <div key={i} className={`diff-line ${cls(l)}`}>{l || ' '}</div>)
            : <p className="muted">No changes between these versions.</p>}
        </div>
        <footer className="diff-actions">
          <button className="ghost-btn" onClick={onClose}>Close</button>
          {state.restoreTo !== null && (
            <button className="primary-btn" onClick={() => onRestore(state.restoreTo!)}>{state.action}</button>
          )}
        </footer>
      </div>
    </div>
  )
}

/** Left column: the authored-doc list with create, filter, rename and delete. */
function DocRail(): JSX.Element {
  const docs = useStore((s) => s.docs)
  const editorDocId = useStore((s) => s.editorDocId)
  const refreshDocs = useStore((s) => s.refreshDocs)
  const openDoc = useStore((s) => s.openDoc)
  const toast = useStore((s) => s.toast)
  const [q, setQ] = useState('')
  const [picker, setPicker] = useState(false)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [renameText, setRenameText] = useState('')

  useEffect(() => { void refreshDocs() }, [refreshDocs])

  const needle = q.trim().toLowerCase()
  const list = needle ? docs.filter((d) => `${d.title} ${d.preview}`.toLowerCase().includes(needle)) : docs

  const createDoc = (format: DocMeta['format']): void => {
    setPicker(false)
    void api.docs.create({ title: 'Untitled', format })
      .then((d) => { openDoc(d.id); void refreshDocs() })
      .catch((e) => toast((e as Error).message, 'error'))
  }
  const remove = (id: string): void => {
    void api.docs.delete(id).then(() => {
      const s = useStore.getState()
      if (s.editorDocId === id) {
        const rest = s.docs.filter((d) => d.id !== id)
        if (rest[0]) s.openDoc(rest[0].id)
        else useStore.setState({ editorDocId: null })
      }
      void refreshDocs()
    }).catch((e) => toast((e as Error).message, 'error'))
  }
  const commitRename = (id: string): void => {
    const t = renameText.trim()
    setRenaming(null)
    if (!t) return
    void api.docs.update(id, { title: t }).then(() => void refreshDocs())
      .catch((e) => toast((e as Error).message, 'error'))
  }

  return (
    <aside className="doc-rail">
      <div className="doc-rail-head">
        <div className="doc-new">
          <button className="ghost-btn" onClick={() => setPicker(!picker)}>
            <FilePlus2 size={14} /> New document <ChevronDown size={12} />
          </button>
          {picker && (
            <div className="doc-format-menu">
              {FORMATS.map((f) => <button key={f.format} onClick={() => createDoc(f.format)}>{f.label}</button>)}
            </div>
          )}
        </div>
        <label className="search"><Search size={13} /><input placeholder="Filter documents" value={q} onChange={(e) => setQ(e.target.value)} /></label>
      </div>
      <div className="doc-rail-list">
        {list.map((d) => (
          <div key={d.id} className={`doc-row ${d.id === editorDocId ? 'active' : ''}`} onClick={() => openDoc(d.id)}>
            {renaming === d.id ? (
              <input autoFocus value={renameText} onClick={(e) => e.stopPropagation()}
                onChange={(e) => setRenameText(e.target.value)}
                onBlur={() => commitRename(d.id)}
                onKeyDown={(e) => { if (e.key === 'Enter') commitRename(d.id); if (e.key === 'Escape') setRenaming(null) }} />
            ) : (
              <>
                <div className="doc-row-main">
                  <span className="doc-row-title">{d.title}</span>
                  <span className="doc-row-date">{ago(d.updated_at)}</span>
                </div>
                <span className="doc-row-actions">
                  <button className="icon-btn sm" title="Rename"
                    onClick={(e) => { e.stopPropagation(); setRenaming(d.id); setRenameText(d.title) }}><Pencil size={12} /></button>
                  <button className="icon-btn sm danger" title="Delete"
                    onClick={(e) => { e.stopPropagation(); remove(d.id) }}><Trash2 size={12} /></button>
                </span>
              </>
            )}
          </div>
        ))}
        {!list.length && <p className="doc-rail-empty">{needle ? 'No matches.' : 'No documents yet.'}</p>}
      </div>
    </aside>
  )
}

/** The open document: header controls, the debounced textarea, and the agent bar as its sibling column. */
function EditorPane({ docId, googleOk }: { docId: string; googleOk: boolean | null }): JSX.Element {
  const agentBarOpen = useStore((s) => s.agentBarOpen)
  const toggleAgentBar = useStore((s) => s.toggleAgentBar)
  const refreshDocs = useStore((s) => s.refreshDocs)
  const toast = useStore((s) => s.toast)
  const [doc, setDoc] = useState<Doc | null>(null)
  const [body, setBody] = useState('')
  const [title, setTitle] = useState('')
  const [preview, setPreview] = useState(false)
  /** null = the History dropdown is closed. */
  const [history, setHistory] = useState<DocVersion[] | null>(null)
  const [diff, setDiff] = useState<DiffState | null>(null)
  const [conflict, setConflict] = useState(false)
  const [backupErr, setBackupErr] = useState('')
  const [backingUp, setBackingUp] = useState(false)
  const [err, setErr] = useState('')
  /** Unsaved body, or null when the doc on the server matches what is on screen (note.tsx pattern). */
  const pending = useRef<string | null>(null)

  useEffect(() => {
    let alive = true
    void api.docs.get(docId)
      .then((d) => { if (!alive) return; setDoc(d); setBody(d.body); setTitle(d.title) })
      .catch((e) => { if (alive) setErr((e as Error).message) })
    return () => { alive = false }
  }, [docId])

  const save = useCallback((next: string): void => {
    pending.current = null
    void api.docs.update(docId, { body: next })
      .then((d) => {
        setDoc(d)
        void useStore.getState().refreshDocs()
      })
      .catch((e) => useStore.getState().toast(`Save failed: ${(e as Error).message}`, 'error'))
  }, [docId])

  // The debounce: an edit followed by another keystroke costs one PUT, not two.
  useEffect(() => {
    if (pending.current === null) return
    const t = setTimeout(() => { if (pending.current !== null) save(pending.current) }, SAVE_MS)
    return () => clearTimeout(t)
  }, [body, save])
  // Switching docs or leaving the view mid-edit still writes: the effect above only cleared its timer.
  useEffect(() => () => { if (pending.current !== null) save(pending.current) }, [save])

  const edit = (v: string): void => {
    pending.current = v
    setBody(v)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key !== 'Tab') return
    e.preventDefault()
    const el = e.currentTarget
    const start = el.selectionStart
    edit(`${el.value.slice(0, start)}  ${el.value.slice(el.selectionEnd)}`)
    requestAnimationFrame(() => { el.selectionStart = el.selectionEnd = start + 2 })
  }

  const commitTitle = (): void => {
    const t = title.trim() || 'Untitled'
    setTitle(t)
    if (!doc || t === doc.title) return
    void api.docs.update(docId, { title: t })
      .then((d) => { setDoc((cur) => (cur ? { ...cur, title: d.title, updated_at: d.updated_at } : cur)); void refreshDocs() })
      .catch((e) => toast((e as Error).message, 'error'))
  }

  const openHistory = (): void => {
    if (history) return setHistory(null)
    void api.docs.versions(docId).then(setHistory).catch((e) => toast((e as Error).message, 'error'))
  }
  const showVersionDiff = (n: number): void => {
    if (!doc) return
    setHistory(null)
    void api.docs.diff(docId, n, doc.version)
      .then((d) => setDiff({ title: `v${n} → v${doc.version} (current)`, text: d.diff, restoreTo: n === doc.version ? null : n, action: 'Restore' }))
      .catch((e) => toast((e as Error).message, 'error'))
  }
  const restore = (n: number): void => {
    void api.docs.restore(docId, n)
      .then((d) => {
        pending.current = null
        setDoc(d); setBody(d.body); setTitle(d.title); setDiff(null); setConflict(false)
        void refreshDocs()
      })
      .catch((e) => toast(`Restore failed: ${(e as Error).message}`, 'error'))
  }

  const backup = (): void => {
    setBackingUp(true)
    setBackupErr('')
    void api.docs.backup(docId)
      .then((m) => {
        setDoc((cur) => (cur ? { ...cur, drive_file_id: m.drive_file_id, drive_synced_at: m.drive_synced_at } : cur))
        toast('Backed up to Google Drive')
      })
      .catch((e) => setBackupErr((e as Error).message))
      .finally(() => setBackingUp(false))
  }

  /** Server body over local state, silently. Only safe when nothing unsaved is on screen — or chosen. */
  const loadTheirs = useCallback((): void => {
    void api.docs.get(docId).then((d) => {
      pending.current = null
      setDoc(d); setBody(d.body); setTitle(d.title); setConflict(false)
      void useStore.getState().refreshDocs()
    }).catch(() => undefined)
  }, [docId])

  // The agent wrote a version. A clean buffer just refetches; a dirty one gets the choice.
  const onAgentEdit = useCallback((): void => {
    if (pending.current !== null) return setConflict(true)
    loadTheirs()
  }, [loadTheirs])

  const words = useMemo(() => (body.trim() ? body.trim().split(/\s+/).length : 0), [body])

  if (err) return <section className="editor-pane"><p className="empty-hint big">{err}</p></section>
  if (!doc) return <section className="editor-pane"><p className="empty-hint big">Loading…</p></section>

  return (
    <>
      <section className="editor-pane">
        <div className="editor-head">
          <input className="editor-title" value={title} placeholder="Untitled"
            onChange={(e) => setTitle(e.target.value)}
            onBlur={commitTitle}
            onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }} />
          <span className="tag">{doc.format}</span>
          <span className="editor-count">{words} words · {body.length} chars</span>
          {doc.format === 'md' && (
            <button className={preview ? 'icon-btn on' : 'icon-btn'} title={preview ? 'Back to editing' : 'Preview'}
              onClick={() => setPreview(!preview)}><Eye size={15} /></button>
          )}
          <div className="editor-menu-wrap">
            <button className={history ? 'icon-btn on' : 'icon-btn'} title="History" onClick={openHistory}><History size={15} /></button>
            {history && (
              <div className="editor-menu">
                {history.length ? history.map((v) => (
                  <button key={v.version} onClick={() => showVersionDiff(v.version)}>
                    <b>v{v.version}</b>
                    <span className="v-source">{v.source}</span>
                    <span className="v-when">{ago(v.created_at)}</span>
                  </button>
                )) : <p>No versions yet.</p>}
              </div>
            )}
          </div>
          <button className="ghost-btn" disabled={backingUp || googleOk === false}
            title={googleOk === false ? 'Connect Google in Settings to back up' : 'Back up to Google Drive'}
            onClick={backup}>
            <CloudUpload size={14} />
            {backingUp ? 'Backing up…' : doc.drive_synced_at ? `Backed up ${ago(doc.drive_synced_at)}` : 'Back up'}
          </button>
          <button className={agentBarOpen ? 'icon-btn on' : 'icon-btn'} title="Agent (⌘I)" onClick={toggleAgentBar}><Sparkles size={15} /></button>
        </div>
        {backupErr && <div className="editor-notice error">{backupErr}</div>}
        {conflict && (
          <div className="editor-notice">
            Agent edited this document —
            <button className="link" onClick={loadTheirs}>Load theirs</button>
            <button className="link" onClick={() => setConflict(false)}>Keep mine</button>
          </div>
        )}
        {preview && doc.format === 'md' ? (
          <div className="editor-preview markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={SAFE_MD}>{body}</ReactMarkdown>
          </div>
        ) : (
          <textarea className="editor-textarea" value={body} spellCheck={false} placeholder="Start writing…"
            onChange={(e) => edit(e.target.value)} onKeyDown={onKeyDown} />
        )}
      </section>
      {agentBarOpen && <AgentBar doc={doc} onAgentEdit={onAgentEdit} onOpenDiff={setDiff} />}
      {diff && <DiffModal state={diff} onRestore={restore} onClose={() => setDiff(null)} />}
    </>
  )
}

export default function EditorView(): JSX.Element {
  const editorDocId = useStore((s) => s.editorDocId)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const toggleSidebar = useStore((s) => s.toggleSidebar)
  /** null until the one status check lands; false disables Back up with a hint. */
  const [googleOk, setGoogleOk] = useState<boolean | null>(null)

  useEffect(() => {
    let alive = true
    void api.google.status()
      .then((s) => { if (alive) setGoogleOk(s.connected && !s.needs_reauth) })
      .catch(() => { if (alive) setGoogleOk(false) })
    return () => { alive = false }
  }, [])

  return (
    <main className="page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><PenLine size={16} /> Editor</h2>
      </header>
      <div className="page-body editor-body">
        <DocRail />
        {editorDocId
          ? <EditorPane key={editorDocId} docId={editorDocId} googleOk={googleOk} />
          : <section className="editor-pane"><p className="empty-hint big">No document open. Create one from the rail.</p></section>}
      </div>
    </main>
  )
}
