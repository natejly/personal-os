import { useEffect, useMemo, useRef, useState } from 'react'
import {
  FileText, NotebookPen, Plus, PanelLeftOpen, X, History, Columns2, Eye, Pencil,
  Sparkles, Save, Link2, Link2Off, ChevronDown, Folder
} from 'lucide-react'
import { useStore, type Scope } from '../store'
import type { Doc } from '@shared/types'
import MarkdownEditor from './MarkdownEditor'
import MarkdownPreview from './MarkdownPreview'
import DiffView from './DiffView'
import ScopeSelect from './ScopeSelect'
import DocTree from './DocTree'
import { clip, lines, usePageContext } from '../lib/pageContext'
import '../styles/docs.css'

const fmtWhen = (ts: number): string => {
  const d = new Date(ts * 1000)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return d.getTime() >= today.getTime()
    ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    : d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

export default function DocsView(): JSX.Element {
  const docs = useStore((s) => s.docs)
  const docFolders = useStore((s) => s.docFolders)
  const activeDoc = useStore((s) => s.activeDoc)
  const docDraft = useStore((s) => s.docDraft)
  const docTabs = useStore((s) => s.docTabs)
  const docRevisions = useStore((s) => s.docRevisions)
  const docMode = useStore((s) => s.docMode)
  const docSaving = useStore((s) => s.docSaving)
  const libraryScope = useStore((s) => s.libraryScope)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const {
    refreshDocs, openDoc, closeDocTab, createDoc, editDoc, flushDoc, renameDoc, setDocStar, setDocFolder,
    deleteDoc, setDocMode, acceptRevision, rejectRevision, restoreRevision, toggleSidebar, setLibraryScope
  } = useStore()

  const [query, setQuery] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [linked, setLinked] = useState(true)
  const [editFrac, setEditFrac] = useState<number | null>(null)
  const [titleDraft, setTitleDraft] = useState<string | null>(null)
  // Non-null while "New folder…" is being typed. An Electron renderer has no window.prompt, so the
  // picker turns into a text input in place rather than asking for the name in a dialog.
  const [folderDraft, setFolderDraft] = useState<string | null>(null)
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
  // Every folder that exists, plus the open doc's own: the list may be filtered, and an empty folder
  // is a real destination the docs themselves cannot vouch for.
  const folders = useMemo(() => {
    const set = new Set([...docFolders.map((f) => f.path), ...docs.map((d) => d.folder).filter(Boolean)])
    if (activeDoc?.folder) set.add(activeDoc.folder)
    return [...set].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
  }, [docFolders, docs, activeDoc])

  // Linked scrolling: the preview follows the editor's fraction of the way down.
  useEffect(() => {
    const el = previewRef.current
    if (!linked || el == null || editFrac == null) return
    const range = el.scrollHeight - el.clientHeight
    if (range > 0) el.scrollTop = editFrac * range
  }, [editFrac, linked])

  const dirty = docDraft !== null && docDraft !== activeDoc?.content

  // ⌘I over a doc answers about that doc: the text as it stands in the editor, unsaved edits and all.
  usePageContext(() => (activeDoc
    ? {
        view: 'docs',
        label: `Doc “${activeDoc.title || 'Untitled'}”`,
        detail: `The doc is open in the editor${dirty ? ' with unsaved edits' : ''}${activeDoc.folder ? `, in the folder “${activeDoc.folder}”` : ''}. Its id is \`${activeDoc.id}\` — revise it with doc_edit. The user sees the diff in the chat; it is applied only when document edits are set to accept all.\n\n\`\`\`markdown\n${clip(body)}\n\`\`\``,
        refs: [{ kind: 'doc', id: activeDoc.id, name: activeDoc.title }],
        hints: ['Summarise this doc', 'Tighten the writing', 'Pull out the action items as todos']
      }
    : {
        view: 'docs',
        label: 'Docs',
        detail: `No doc is open. The list shows:\n${lines(docs, (d) => `“${d.title || 'Untitled'}” (\`${d.id}\`)${d.folder ? ` in ${d.folder}` : ''}`)}`,
        refs: docs.slice(0, 40).map((d) => ({ kind: 'doc', id: d.id, name: d.title })),
        hints: ['What have I been writing about?', 'Start a doc for this week\u2019s plan']
      }), [activeDoc?.id, activeDoc?.title, activeDoc?.folder, body, dirty, docs])

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
          <DocTree
            docs={docs} activeId={activeDoc?.id ?? null} query={query} onQuery={setQuery}
            showScope={scope === 'all'} projectId={scope === 'all' || scope === 'personal' ? null : scope}
          />
        </aside>

        {!activeDoc ? (
          <section className="docs-empty">
            <FileText size={30} />
            <h2>Nothing open</h2>
            <p className="muted">Pick a doc on the left, or start a new one. Write markdown; wrap maths in <code>$…$</code> or <code>$$…$$</code>.</p>
            <p className="muted small">
              In a chat, the assistant revises these with <code>doc_edit</code>. You see every change as a diff.
              Ask, the default, waits for you; Accept all in Settings writes it.
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
              <label className="model-picker doc-folder-pick" title="Folder">
                <Folder size={13} />
                {folderDraft !== null ? (
                  <input
                    autoFocus
                    value={folderDraft}
                    placeholder="Folder name"
                    onChange={(e) => setFolderDraft(e.target.value)}
                    onBlur={() => {
                      const name = folderDraft.trim()
                      if (name) void setDocFolder(activeDoc.id, name)
                      setFolderDraft(null)
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') e.currentTarget.blur()
                      if (e.key === 'Escape') setFolderDraft(null)
                    }}
                  />
                ) : (
                  <>
                    <select
                      value={activeDoc.folder || ''}
                      onChange={(e) => {
                        const v = e.target.value
                        if (v === '__new__') setFolderDraft('')
                        else void setDocFolder(activeDoc.id, v)
                      }}
                    >
                      <option value="">No folder</option>
                      {folders.map((f) => <option key={f} value={f}>{f}</option>)}
                      <option value="__new__">New folder…</option>
                    </select>
                    <ChevronDown size={12} />
                  </>
                )}
              </label>
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
