import { useEffect, useMemo, useRef, useState } from 'react'
import {
  FileText, NotebookPen, Plus, Trash2, Star, Search, PanelLeftOpen, X, History, Columns2, Eye, Pencil,
  Sparkles, Save, Link2, Link2Off, ChevronRight
} from 'lucide-react'
import { useStore, type Scope } from '../store'
import type { Doc } from '@shared/types'
import MarkdownEditor from './MarkdownEditor'
import MarkdownPreview from './MarkdownPreview'
import DiffView from './DiffView'
import ProjectChip from './ProjectChip'
import ScopeSelect from './ScopeSelect'
import '../styles/docs.css'

const fmtWhen = (ts: number): string => {
  const d = new Date(ts * 1000)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return d.getTime() >= today.getTime()
    ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    : d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

/** The doc list: folders first, then loose docs, starred pinned to the top within each group. */
function DocList({ docs, activeId, query, onQuery, onOpen, onDelete, onStar, showScope }: {
  docs: Doc[]
  activeId: string | null
  query: string
  onQuery: (q: string) => void
  onOpen: (id: string) => void
  onDelete: (id: string) => void
  onStar: (id: string, v: boolean) => void
  showScope: boolean
}): JSX.Element {
  const groups = useMemo(() => {
    const m = new Map<string, Doc[]>()
    for (const d of docs) {
      const k = d.folder || ''
      const arr = m.get(k)
      if (arr) arr.push(d)
      else m.set(k, [d])
    }
    return [...m.entries()].sort((a, b) => (a[0] === '' ? 1 : b[0] === '' ? -1 : a[0].localeCompare(b[0])))
  }, [docs])
  const [shut, setShut] = useState<Record<string, boolean>>({})

  return (
    <div className="doc-tree">
      <label className="search mini"><Search size={12} /><input placeholder="Search docs" value={query} onChange={(e) => onQuery(e.target.value)} /></label>
      {docs.length === 0 && <p className="empty-hint">{query ? 'No matches.' : 'No docs yet.'}</p>}
      {groups.map(([folder, items]) => (
        <section key={folder || '_loose'}>
          {folder && (
            <button className="doc-folder" onClick={() => setShut((x) => ({ ...x, [folder]: !x[folder] }))}>
              <ChevronRight size={11} className={shut[folder] ? undefined : 'rot90'} />{folder}<span className="count">{items.length}</span>
            </button>
          )}
          {!shut[folder] && items.map((d) => (
            <div key={d.id} className={`doc-row ${d.id === activeId ? 'active' : ''}`} onClick={() => onOpen(d.id)} role="button" tabIndex={0}>
              <FileText size={13} className="doc-row-icon" />
              <span className="doc-row-main">
                <span className="doc-row-title">
                  {d.title || 'Untitled'}
                  {typeof d.pending === 'number' && d.pending > 0 && (
                    <span className="doc-pending" title={`${d.pending} assistant edit${d.pending === 1 ? '' : 's'} awaiting review`}>
                      <Sparkles size={9} />{d.pending}
                    </span>
                  )}
                </span>
                <span className="doc-row-meta">
                  {showScope && <ProjectChip projectId={d.project_id} showPersonal />}
                  {d.words} words · {fmtWhen(d.updated_at)}
                </span>
              </span>
              <button className={`icon-btn ghost xs ${d.starred ? 'starred' : ''}`} title={d.starred ? 'Unstar' : 'Star'}
                onClick={(e) => { e.stopPropagation(); onStar(d.id, !d.starred) }}>
                <Star size={12} fill={d.starred ? 'currentColor' : 'none'} />
              </button>
              <button className="icon-btn ghost xs danger" title="Delete"
                onClick={(e) => { e.stopPropagation(); if (confirm(`Delete “${d.title}”? Its revision history goes too.`)) onDelete(d.id) }}>
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </section>
      ))}
    </div>
  )
}

export default function DocsView(): JSX.Element {
  const docs = useStore((s) => s.docs)
  const activeDoc = useStore((s) => s.activeDoc)
  const docDraft = useStore((s) => s.docDraft)
  const docTabs = useStore((s) => s.docTabs)
  const docRevisions = useStore((s) => s.docRevisions)
  const docMode = useStore((s) => s.docMode)
  const docSaving = useStore((s) => s.docSaving)
  const libraryScope = useStore((s) => s.libraryScope)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const {
    refreshDocs, openDoc, closeDocTab, createDoc, editDoc, flushDoc, renameDoc, setDocStar, deleteDoc,
    setDocMode, acceptRevision, rejectRevision, restoreRevision, toggleSidebar, setLibraryScope
  } = useStore()

  const [query, setQuery] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [linked, setLinked] = useState(true)
  const [editFrac, setEditFrac] = useState<number | null>(null)
  const [titleDraft, setTitleDraft] = useState<string | null>(null)
  const previewRef = useRef<HTMLDivElement>(null)

  const scope: Scope = libraryScope
  useEffect(() => { void refreshDocs(query) }, [refreshDocs, query, scope])
  // Anything still buffered belongs on disk before this view goes away.
  useEffect(() => () => { void flushDoc() }, [flushDoc])

  // The editor shows the buffer while typing and the saved body otherwise.
  const body = docDraft ?? activeDoc?.content ?? ''
  const pending = activeDoc?.pending ?? []
  const applied = useMemo(() => docRevisions.filter((r) => r.status !== 'pending'), [docRevisions])
  const tabDocs = useMemo(
    () => docTabs.map((id) => docs.find((d) => d.id === id) ?? (activeDoc?.id === id ? activeDoc : null)).filter(Boolean) as Doc[],
    [docTabs, docs, activeDoc]
  )

  // Linked scrolling: the preview follows the editor's fraction of the way down.
  useEffect(() => {
    const el = previewRef.current
    if (!linked || el == null || editFrac == null) return
    const range = el.scrollHeight - el.clientHeight
    if (range > 0) el.scrollTop = editFrac * range
  }, [editFrac, linked])

  const dirty = docDraft !== null && docDraft !== activeDoc?.content

  return (
    <main className="page docs-page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" title="Show sidebar (⌘B)" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><NotebookPen size={16} /> Docs</h2>
        <div className="no-drag header-right">
          <ScopeSelect value={scope} onChange={(s) => void setLibraryScope(s)} />
          <button className="primary-btn" onClick={() => void createDoc({ project_id: scope === 'all' || scope === 'personal' ? null : scope })}>
            <Plus size={14} /> New doc
          </button>
        </div>
      </header>

      <div className="docs-body">
        <aside className="docs-side">
          <DocList
            docs={docs} activeId={activeDoc?.id ?? null} query={query} onQuery={setQuery}
            onOpen={(id) => void openDoc(id)} onDelete={(id) => void deleteDoc(id)}
            onStar={(id, v) => void setDocStar(id, v)} showScope={scope === 'all'}
          />
        </aside>

        {!activeDoc ? (
          <section className="docs-empty">
            <FileText size={30} />
            <h2>Nothing open</h2>
            <p className="muted">Pick a doc on the left, or start a new one. Write markdown; wrap maths in <code>$…$</code> or <code>$$…$$</code>.</p>
            <p className="muted small">
              In a chat, the assistant can read and revise these with <code>doc_read</code> and <code>doc_edit</code>.
              Its edits arrive here as a diff you accept or reject — nothing is rewritten behind your back.
            </p>
            <button className="primary-btn" onClick={() => void createDoc({})}><Plus size={14} /> New doc</button>
          </section>
        ) : (
          <section className="docs-main">
            {tabDocs.length > 1 && (
              <div className="doc-tabs">
                {tabDocs.map((d) => (
                  <button key={d.id} className={`doc-tab ${d.id === activeDoc.id ? 'active' : ''}`} onClick={() => void openDoc(d.id)}>
                    <FileText size={11} />{d.title || 'Untitled'}
                    <span className="tab-x" role="button" title="Close" onClick={(e) => { e.stopPropagation(); closeDocTab(d.id) }}><X size={10} /></span>
                  </button>
                ))}
              </div>
            )}

            <div className="doc-toolbar">
              <input
                className="doc-title-input"
                value={titleDraft ?? activeDoc.title}
                onChange={(e) => setTitleDraft(e.target.value)}
                onBlur={() => { if (titleDraft !== null && titleDraft !== activeDoc.title) void renameDoc(activeDoc.id, titleDraft); setTitleDraft(null) }}
                onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); if (e.key === 'Escape') setTitleDraft(null) }}
              />
              <span className="doc-save-state">
                {docSaving ? 'Saving…' : dirty ? 'Unsaved' : 'Saved'}
              </span>
              <span className="spacer" />
              <div className="seg">
                <button className={docMode === 'edit' ? 'on' : ''} title="Editor only" onClick={() => setDocMode('edit')}><Pencil size={13} /></button>
                <button className={docMode === 'split' ? 'on' : ''} title="Editor and preview" onClick={() => setDocMode('split')}><Columns2 size={13} /></button>
                <button className={docMode === 'preview' ? 'on' : ''} title="Preview only" onClick={() => setDocMode('preview')}><Eye size={13} /></button>
              </div>
              {docMode === 'split' && (
                <button className="icon-btn ghost" title={linked ? 'Unlink scrolling' : 'Link scrolling'} onClick={() => setLinked((l) => !l)}>
                  {linked ? <Link2 size={14} /> : <Link2Off size={14} />}
                </button>
              )}
              <button className="icon-btn ghost" title="Save now (⌘S)" onClick={() => void flushDoc()}><Save size={14} /></button>
              <button className={`icon-btn ghost ${historyOpen ? 'on' : ''}`} title="Revision history" onClick={() => setHistoryOpen((h) => !h)}>
                <History size={14} />
                {pending.length > 0 && <span className="dot-badge">{pending.length}</span>}
              </button>
            </div>

            {pending.length > 0 && !historyOpen && (
              <div className="doc-review">
                <h3><Sparkles size={13} /> {pending.length} assistant edit{pending.length === 1 ? '' : 's'} to review</h3>
                {pending.map((r) => (
                  <DiffView
                    key={r.id} revision={r} current={activeDoc.content}
                    onAccept={() => void acceptRevision(r.id)}
                    onReject={() => void rejectRevision(r.id)}
                  />
                ))}
              </div>
            )}

            <div className={`doc-panes ${docMode}`}>
              {docMode !== 'preview' && (
                <MarkdownEditor
                  value={body}
                  onChange={editDoc}
                  onSave={() => void flushDoc()}
                  placeholder={'# Title\n\nWrite in markdown. Maths goes in $…$ or $$…$$.'}
                  onScrollFraction={linked && docMode === 'split' ? setEditFrac : undefined}
                />
              )}
              {docMode !== 'edit' && (
                <div className="docs-render markdown" ref={previewRef}>
                  {body.trim() ? <MarkdownPreview source={body} /> : <p className="muted">Nothing to preview yet.</p>}
                </div>
              )}
            </div>
          </section>
        )}

        {activeDoc && historyOpen && (
          <aside className="docs-history">
            <header><h3>History</h3><button className="icon-btn ghost" onClick={() => setHistoryOpen(false)}><X size={14} /></button></header>
            <p className="muted small pad">
              Every save is a revision. Assistant edits sit at the top until you accept them.
            </p>
            {pending.map((r) => (
              <DiffView key={r.id} revision={r} current={activeDoc.content}
                onAccept={() => void acceptRevision(r.id)} onReject={() => void rejectRevision(r.id)} />
            ))}
            {applied.length === 0 && pending.length === 0 && <p className="empty-hint">No revisions yet.</p>}
            {applied.map((r) => (
              <DiffView key={r.id} revision={r} onRestore={() => {
                if (confirm('Restore the document to this version? The current text is kept in the history.')) void restoreRevision(r.id)
              }} />
            ))}
          </aside>
        )}
      </div>
    </main>
  )
}
