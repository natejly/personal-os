import { useEffect, useMemo, useRef, useState } from 'react'
import {
  FileText, Files, Plus, PanelLeftOpen, X, History, Columns2, Eye, Pencil,
  Sparkles, Save, Link2, Link2Off, ChevronDown, Folder, FolderKanban, FolderTree
} from 'lucide-react'
import { useStore } from '../store'
import type { Doc } from '@shared/types'
import MarkdownEditor from './MarkdownEditor'
import MarkdownPreview from './MarkdownPreview'
import DiffView from './DiffView'
import DocTree from './DocTree'
import { scopeOf } from '../lib/docTree'
import ResizeHandle from './ResizeHandle'
import { clip, lines, usePageContext } from '../lib/pageContext'
import '../styles/docs.css'
import AppSwitcher from './AppSwitcher'

const TREE_KEY = 'grain.docs.treeOpen'
const treeWasOpen = (): boolean => {
  try { return localStorage.getItem(TREE_KEY) !== '0' } catch { return true }
}

export default function DocsView(): JSX.Element {
  const docs = useStore((s) => s.docs)
  const docFolders = useStore((s) => s.docFolders)
  const projects = useStore((s) => s.projects)
  const activeDoc = useStore((s) => s.activeDoc)
  const docDraft = useStore((s) => s.docDraft)
  const docTitleDraft = useStore((s) => s.docTitleDraft)
  const docTabs = useStore((s) => s.docTabs)
  const docRevisions = useStore((s) => s.docRevisions)
  const docMode = useStore((s) => s.docMode)
  const docSaving = useStore((s) => s.docSaving)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const {
    refreshDocs, openDoc, closeDocTab, createDoc, editDoc, editDocTitle, flushDoc, moveDoc,
    setDocMode, acceptRevision, rejectRevision, restoreRevision, toggleSidebar
  } = useStore()

  const [query, setQuery] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [treeOpen, setTreeOpenState] = useState(treeWasOpen)
  const setTreeOpen = (open: boolean): void => {
    setTreeOpenState(open)
    try { localStorage.setItem(TREE_KEY, open ? '1' : '0') } catch { /* private window */ }
  }
  const [linked, setLinked] = useState(true)
  const [editFrac, setEditFrac] = useState<number | null>(null)
  // Non-null while "New folder…" is being typed in the toolbar. An Electron renderer has no
  // window.prompt, so the picker turns into a text input in place rather than asking in a dialog.
  const [folderDraft, setFolderDraft] = useState<string | null>(null)
  const previewRef = useRef<HTMLDivElement>(null)

  useEffect(() => { void refreshDocs(query) }, [refreshDocs, query])
  // Anything still buffered belongs on disk before this view goes away — and before the window does.
  useEffect(() => {
    const flush = (): void => { void flushDoc() }
    window.addEventListener('pagehide', flush)
    window.addEventListener('blur', flush)
    return () => {
      window.removeEventListener('pagehide', flush)
      window.removeEventListener('blur', flush)
      flush()
    }
  }, [flushDoc])

  // The editor shows the buffer while typing and the saved body otherwise.
  const body = docDraft ?? activeDoc?.content ?? ''
  const title = docTitleDraft ?? activeDoc?.title ?? ''
  const pending = activeDoc?.pending ?? []
  const applied = useMemo(() => docRevisions.filter((r) => r.status !== 'pending'), [docRevisions])
  const tabDocs = useMemo(
    () => docTabs.map((id) => docs.find((d) => d.id === id) ?? (activeDoc?.id === id ? activeDoc : null)).filter(Boolean) as Doc[],
    [docTabs, docs, activeDoc]
  )
  const scope = activeDoc ? scopeOf(activeDoc) : ''
  const projectName = projects.find((p) => p.id === scope)?.name
  // Every folder of the doc's own project, plus the one it is in: the list may be filtered, and an
  // empty folder is a real destination the docs themselves cannot vouch for.
  const folders = useMemo(() => {
    const set = new Set([
      ...docFolders.filter((f) => f.scope === scope).map((f) => f.path),
      ...docs.filter((d) => scopeOf(d) === scope).map((d) => d.folder).filter(Boolean)
    ])
    if (activeDoc?.folder) set.add(activeDoc.folder)
    return [...set].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
  }, [docFolders, docs, activeDoc, scope])

  // Linked scrolling: the preview follows the editor's fraction of the way down.
  useEffect(() => {
    const el = previewRef.current
    if (!linked || el == null || editFrac == null) return
    const range = el.scrollHeight - el.clientHeight
    if (range > 0) el.scrollTop = editFrac * range
  }, [editFrac, linked])

  const dirty = (docDraft !== null && docDraft !== activeDoc?.content) ||
    (docTitleDraft !== null && docTitleDraft !== activeDoc?.title)

  // ⌘I over a doc answers about that doc: the text as it stands in the editor, unsaved edits and all.
  usePageContext(() => (activeDoc
    ? {
        view: 'docs',
        label: `Doc “${activeDoc.title || 'Untitled'}”`,
        detail: `Open${dirty ? ', unsaved edits' : ''}. ${projectName ? `Project “${projectName}”` : 'Personal'}${activeDoc.folder ? ` / ${activeDoc.folder}` : ''}. Id \`${activeDoc.id}\`. Revise with doc_edit. The user reviews the diff unless document edits are set to accept all.\n\n\`\`\`markdown\n${clip(body)}\n\`\`\``,
        refs: [{ kind: 'doc', id: activeDoc.id, name: activeDoc.title }],
        hints: ['Summarise this doc', 'Tighten the writing', 'Pull out the action items as todos']
      }
    : {
        view: 'docs',
        label: 'Files',
        detail: `No doc is open. Files are grouped by project — Personal plus one folder per project. The list shows:\n${lines(docs, (d) => `“${d.title || 'Untitled'}” (\`${d.id}\`)${d.project_id ? ` in project ${d.project_id}` : ' in Personal'}${d.folder ? `/${d.folder}` : ''}`)}`,
        refs: docs.slice(0, 40).map((d) => ({ kind: 'doc', id: d.id, name: d.title })),
        hints: ['What have I been writing about?', 'Start a doc for this week’s plan']
      }), [activeDoc?.id, activeDoc?.title, activeDoc?.folder, projectName, body, dirty, docs])

  return (
    <main className="page docs-page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" title="Show sidebar (⌘B)" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <button
          className={`icon-btn no-drag ${treeOpen ? 'on' : ''}`} title={treeOpen ? 'Hide file tree' : 'Show file tree'}
          aria-label="Toggle file tree" aria-pressed={treeOpen} onClick={() => setTreeOpen(!treeOpen)}
        ><FolderTree size={16} /></button>
        <h2><Files size={16} /> Files</h2>
        <div className="no-drag header-right">
          <button className="primary-btn" onClick={() => void createDoc({})}>
            <Plus size={14} /> New doc
          </button>
        </div>
        <AppSwitcher />
      </header>

      <div className={`docs-body ${treeOpen ? '' : 'tree-hidden'}`}>
        {/* Both side panels scroll, so their handles live on the body, pinned to the column edges. */}
        {treeOpen && (
          <>
            <aside className="docs-side">
              <DocTree docs={docs} activeId={activeDoc?.id ?? null} query={query} onQuery={setQuery} />
            </aside>
            <ResizeHandle id="docs-tree-w" defaultSize={240} min={170} max={480} grows="right" onCollapse={() => setTreeOpen(false)} label="File tree width" className="docs-tree-edge" />
          </>
        )}
        {activeDoc && historyOpen && (
          <ResizeHandle id="docs-history-w" defaultSize={380} min={280} max={720} grows="left" onCollapse={() => setHistoryOpen(false)} label="History width" className="docs-history-edge" />
        )}

        {!activeDoc ? (
          <section className="docs-empty">
            <FileText size={30} />
            <h2>Nothing open</h2>
            <p className="muted">Pick a file on the left, or start a new one.</p>
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
                value={title}
                placeholder="Untitled"
                onChange={(e) => editDocTitle(e.target.value)}
                onBlur={() => void flushDoc()}
                onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }}
              />
              <label className="model-picker doc-folder-pick" title="Project">
                <FolderKanban size={13} />
                <select
                  value={scope}
                  onChange={(e) => { setFolderDraft(null); void moveDoc(activeDoc.id, e.target.value, '') }}
                >
                  <option value="">Personal</option>
                  {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
                <ChevronDown size={12} />
              </label>
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
                      if (name) void moveDoc(activeDoc.id, scope, name)
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
                        else void moveDoc(activeDoc.id, scope, v)
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
              <button className="icon-btn ghost" title="Save now (⌘S) — it autosaves anyway" onClick={() => void flushDoc()}><Save size={14} /></button>
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
              {docMode === 'split' && (
                <ResizeHandle id="doc-split" unit="%" defaultSize={50} min={20} max={80} grows="right" label="Editor and preview split" className="doc-split-edge" />
              )}
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
