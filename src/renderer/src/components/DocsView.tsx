import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  FileText, Files, PanelRight, X, Columns2, Eye, Pencil, BookOpen, MessageSquarePlus, Type,
  Sparkles, FolderTree, SlidersHorizontal
} from 'lucide-react'
import { flushDocOnUnload, restoreDocTabs, useStore, type FilesSection } from '../store'
import { api, type DocHit } from '../lib/api'
import type { Doc, DocTypography } from '@shared/types'
import MarkdownEditor from './MarkdownEditor'
import DocFind from './DocFind'
import MarkdownPreview from './MarkdownPreview'
import DiffView from './DiffView'
import DocTree from './DocTree'
import { scopeOf } from '../lib/docTree'
import ResizeHandle from './ResizeHandle'
import { oneLine } from '../lib/emailAsk'
import { fenced, lines, usePageContext } from '../lib/pageContext'
import { PANEL_TABS, parsePanelState, resolveWikiDoc, type PanelState, type PanelTab } from '../lib/docPanel'
import Backlinks from '../features/notes/Backlinks'
import DocOutline from '../features/notes/DocOutline'
import FormatBar from '../features/notes/FormatBar'
import ExportMenu from '../features/notes/ExportMenu'
import { expandTemplate, userTemplates } from '../features/notes/templates'
import NewDocMenu from '../features/notes/NewDocMenu'
import type { MarkdownEditorHandle } from '../features/notes/handle'
import type { SlashCommand } from '../features/notes/slash'
import { toggleTaskAt } from '../features/notes/tasks'
import { CommentsPanel, DocCommentFab, useDocComments } from '../features/notes/DocComments'
import { makeAnchor } from '../features/notes/comments'
import TypographyControls from '../features/notes/TypographyMenu'
import { effectiveTypography, typographyStyle } from '../features/notes/typography'
import '../styles/docs.css'
import AppSwitcher from './AppSwitcher'
import DocumentsView from './DocumentsView'
import ScopeSelect from './ScopeSelect'
import SidebarToggle from './SidebarToggle'

const SECTIONS: [FilesSection, string][] = [['notes', 'Notes'], ['uploads', 'Uploads']]

const PANEL_KEY = 'grain.docs.panel'
const readPanel = (): PanelState => {
  try { return parsePanelState(localStorage.getItem(PANEL_KEY)) } catch { return parsePanelState(null) }
}
const PANEL_LABEL: Record<PanelTab, string> = { outline: 'Outline', comments: 'Comments', links: 'Links', history: 'History' }

// A doc opens in the reading view; Edit is a choice remembered per doc (the edit/split/preview mode
// stays one global preference, as before). The newest 200 ids are kept.
const EDITING_KEY = 'grain.docs.editing'
const readEditing = (): string[] => {
  try {
    const v = JSON.parse(localStorage.getItem(EDITING_KEY) ?? '[]') as unknown
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
  } catch { return [] }
}

const TREE_KEY = 'grain.docs.treeOpen'
const treeWasOpen = (): boolean => {
  try { return localStorage.getItem(TREE_KEY) !== '0' } catch { return true }
}

const flagOn = (key: string): boolean => {
  try { return localStorage.getItem(key) === '1' } catch { return false }
}

/** Writing-view toggles, folded into one menu so the toolbar stays quiet. */
function ViewMenu({ children, icon, title = 'View options', className = '' }: { children: React.ReactNode; icon?: React.ReactNode; title?: string; className?: string }): JSX.Element {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent): void => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent): void => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [open])
  return (
    <div className="newdoc" ref={root}>
      <button className="icon-btn ghost" title={title} aria-label={title} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{icon ?? <SlidersHorizontal size={14} />}</button>
      {open && <div className={`notes-menu right ${className}`} role="menu">{children}</div>}
    </div>
  )
}

export default function DocsView(): JSX.Element {
  const docs = useStore((s) => s.docs)
  const projects = useStore((s) => s.projects)
  const activeDoc = useStore((s) => s.activeDoc)
  const docDraft = useStore((s) => s.docDraft)
  const docTitleDraft = useStore((s) => s.docTitleDraft)
  const docTabs = useStore((s) => s.docTabs)
  const docRevisions = useStore((s) => s.docRevisions)
  const docMode = useStore((s) => s.docMode)
  const docSaving = useStore((s) => s.docSaving)
  const docFocusId = useStore((s) => s.docFocusId)
  const section = useStore((s) => s.filesSection)
  const libraryScope = useStore((s) => s.libraryScope)
  const { openFiles, setLibraryScope } = useStore()
  const {
    refreshDocs, openDoc, closeDocTab, createDoc, editDoc, editDocTitle, flushDoc,
    setDocMode, acceptRevision, rejectRevision, restoreRevision, openDailyNote, setDocTypography
  } = useStore()
  const globalType = useStore((s) => s.settings.docTypography)

  const [query, setQuery] = useState('')
  // One tabbed panel in the right-hand column (Outline, Comments, Links, History).
  const [panel, setPanelState] = useState<PanelState>(readPanel)
  const setPanel = useCallback((next: PanelState): void => {
    setPanelState(next)
    try { localStorage.setItem(PANEL_KEY, JSON.stringify(next)) } catch { /* private window */ }
  }, [])
  const panelOpen = panel.open
  const historyOpen = panelOpen && panel.tab === 'history'
  const editor = useRef<MarkdownEditorHandle>(null)
  const pendingJump = useRef<number | null>(null)
  const [caretLine, setCaretLine] = useState(1)
  const [treeOpen, setTreeOpenState] = useState(treeWasOpen)
  const setTreeOpen = (open: boolean): void => {
    setTreeOpenState(open)
    try { localStorage.setItem(TREE_KEY, open ? '1' : '0') } catch { /* private window */ }
  }
  const [writeFlags, setWriteFlags] = useState({ focus: flagOn('grain.docs.focus'), typewriter: flagOn('grain.docs.typewriter') })
  const toggleFlag = (k: 'focus' | 'typewriter'): void => {
    const next = !writeFlags[k]
    setWriteFlags({ ...writeFlags, [k]: next })
    try { localStorage.setItem(`grain.docs.${k}`, next ? '1' : '0') } catch { /* private window */ }
  }
  const [linked, setLinked] = useState(true)
  // Reading view unless this doc was switched to Edit (⌘E, the toolbar, or a double-click on the text).
  const [editingIds, setEditingIds] = useState<string[]>(readEditing)
  const editing = !!activeDoc && editingIds.includes(activeDoc.id)
  const setEditing = useCallback((on: boolean, id = activeDoc?.id): void => {
    if (!id) return
    setEditingIds((ids) => {
      const next = on ? [...ids.filter((x) => x !== id), id].slice(-200) : ids.filter((x) => x !== id)
      try { localStorage.setItem(EDITING_KEY, JSON.stringify(next)) } catch { /* private window */ }
      return next
    })
  }, [activeDoc?.id])
  useEffect(() => {
    if (!activeDoc) return
    const key = (e: KeyboardEvent): void => {
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'e') { e.preventDefault(); setEditing(!editing) }
    }
    document.addEventListener('keydown', key)
    return () => document.removeEventListener('keydown', key)
  }, [activeDoc, editing, setEditing])
  // Which panes are up. The rendered pane is the reading view, and the preview beside or instead of the editor.
  const showEditor = editing && docMode !== 'preview'
  const showRender = !editing || docMode !== 'edit'
  const paneKey = `${activeDoc?.id ?? ''}:${editing ? docMode : 'read'}`
  // Read by the scroll handler, so toggling the link does not hand the editor a new callback.
  const linkedRef = useRef(linked)
  linkedRef.current = linked
  // Non-null while "New folder…" is being typed in the toolbar. An Electron renderer has no
  // window.prompt, so the picker turns into a text input in place rather than asking in a dialog.
  const previewRef = useRef<HTMLDivElement>(null)
  const renderRoot = useCallback(() => previewRef.current, [])

  useEffect(() => { void refreshDocs() }, [refreshDocs])
  useEffect(() => { void restoreDocTabs() }, [])
  // The narrowed list lives here, not in the store: tabs, folders, links and templates read the full `docs`.
  const [matches, setMatches] = useState<Doc[] | null>(null)
  // Re-asked when `docs` changes too, so a doc deleted or renamed mid-search leaves the list. The last
  // answer stays up while the next loads, rather than flashing "No matches." on every keystroke.
  useEffect(() => {
    const q = query.trim()
    if (!q) return setMatches(null)
    let stale = false
    api.docs.list('all', q).then((d) => { if (!stale) setMatches(d) }).catch(() => { /* keep the last answer */ })
    return () => { stale = true }
  }, [query, docs])
  // Ranked hits with a snippet for the tree's search; null until the (debounced) answer arrives.
  const [hits, setHits] = useState<DocHit[] | null>(null)
  useEffect(() => {
    setHits(null)
    const q = query.trim()
    if (!q) return
    let stale = false
    const t = setTimeout(() => { api.docs.search(q).then((h) => { if (!stale) setHits(h) }).catch(() => { /* keep the plain list */ }) }, 200)
    return () => { stale = true; clearTimeout(t) }
  }, [query])
  // Anything still buffered belongs on disk before this view goes away — and before the window does.
  useEffect(() => {
    const flush = (): void => { void flushDoc() }
    window.addEventListener('pagehide', flushDocOnUnload)
    window.addEventListener('blur', flush)
    return () => {
      window.removeEventListener('pagehide', flushDocOnUnload)
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
  // A doc just made by New or Today's note opens in Edit and gets the caret, at its end, so typing can
  // start at once. The editor mounts on the next render, so the jump waits for it.
  useEffect(() => {
    if (!docFocusId || docFocusId !== activeDoc?.id) return
    if (!editing) { setEditing(true); return }
    if (docMode !== 'preview') editor.current?.jumpToLine(Number.MAX_SAFE_INTEGER)
    useStore.setState({ docFocusId: null })
  }, [docFocusId, activeDoc?.id, docMode, editing, setEditing])

  // The type in effect: the doc's own choice over the global default. Set on the pane that holds both views.
  const type = useMemo(() => effectiveTypography(activeDoc?.typography, globalType), [activeDoc?.typography, globalType])
  const typeStyle = useMemo(() => typographyStyle(type), [type])
  const patchType = (patch: DocTypography): void => {
    if (activeDoc) void setDocTypography(activeDoc.id, { ...(activeDoc.typography ?? {}), ...patch })
  }

  // Linked scrolling: the preview follows the editor's fraction of the way down. Written straight to
  // the element: a scroll event per frame through state re-rendered this whole view each time.
  const followEditor = useCallback((f: number): void => {
    const el = previewRef.current
    if (!linkedRef.current || el == null) return
    const range = el.scrollHeight - el.clientHeight
    if (range > 0) el.scrollTop = f * range
  }, [])

  const dirty = (docDraft !== null && docDraft !== activeDoc?.content) ||
    (docTitleDraft !== null && docTitleDraft !== activeDoc?.title)

  const docId = activeDoc?.id ?? ''

  // ---- editor wiring ----
  // Slash-menu entries beyond the editor's own commands. Memoised
  // because the editor re-derives its command list from this array's identity.
  const userTpls = useMemo(() => userTemplates(docs), [docs])
  const extraCommands = useMemo((): SlashCommand[] => (docId
    ? ([
        { id: 'daily', label: 'Daily file', hint: 'today', keywords: ['today', 'journal', 'daily'], run: () => void openDailyNote() }
      ] as SlashCommand[])
    : [] as SlashCommand[]).concat(userTpls.map((t): SlashCommand => ({
      id: `tpl-${t.id}`, label: `Template: ${t.title || 'Untitled'}`, hint: 'template', keywords: ['template', t.title],
      run: (h) => {
        void api.docs.get(t.id).then((full) => {
          const x = expandTemplate(full.content ?? '', { now: new Date(), title: activeDoc?.title })
          h.insertAtCaret(x.text, x.caret)
        }).catch(() => {})
      }
    }))), [docId, openDailyNote, userTpls, activeDoc?.title])
  // A link can point at any other titled doc. The list may be narrowed by the tree's search box; that
  // only shortens the picker.
  const linkTargets = useMemo(
    () => docs.filter((d) => d.id !== docId && d.title.trim()).map((d) => ({ id: d.id, title: d.title })),
    [docs, docId]
  )
  const knownTitles = useMemo(() => new Set(docs.map((d) => d.title).filter((t) => t.trim())), [docs])

  // Comments live on the rendered text. A click on a mark opens the panel on its thread.
  const comments = useDocComments(docId, body, previewRef, paneKey, panelOpen && panel.tab === 'comments',
    () => setPanel({ open: true, tab: 'comments' }))
  const commentOn = (anchor: ReturnType<typeof makeAnchor>): void => {
    comments.startDraft(anchor)
    setPanel({ open: true, tab: 'comments' })
  }

  // An outline jump from the reading or preview-only view brings the editor up first; it mounts on the
  // next render, so the jump waits for it.
  const jumpToLine = (line: number): void => {
    if (!showEditor) {
      pendingJump.current = line
      if (!editing) setEditing(true)
      if (docMode === 'preview') setDocMode('split')
    } else editor.current?.jumpToLine(line)
  }
  // A cited passage opened from chat: jump once its doc is the one on screen.
  const docJump = useStore((s) => s.docJump)
  useEffect(() => {
    if (!docJump || activeDoc?.id !== docJump.docId) return
    useStore.setState({ docJump: null })
    jumpToLine(docJump.line)
  }, [docJump, activeDoc?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!showEditor || pendingJump.current === null) return
    editor.current?.jumpToLine(pendingJump.current)
    pendingJump.current = null
  }, [showEditor])

  // `[[Title]]` in the preview: open the doc, or start it beside the current one. The list can be
  // narrowed by the tree search, so a filtered list is replaced by the full one before deciding "missing".
  const openWikilink = async (target: string): Promise<void> => {
    const name = target.trim()
    if (!name) return
    let all = docs
    if (query) { try { all = await api.docs.list('all', '') } catch { /* fall back to what we have */ } }
    const hit = resolveWikiDoc(all, name, scope, activeDoc?.id)
    if (hit) void openDoc(hit.id)
    else void createDoc({ title: name, project_id: scope || null, folder: activeDoc?.folder ?? '' })
  }

  // ⌘I over a doc answers about that doc: the text as it stands in the editor, unsaved edits and all.
  usePageContext(() => (activeDoc
    ? {
        view: 'docs',
        label: `File “${oneLine(activeDoc.title || 'Untitled', 80)}”`,
        detail: `Open${dirty ? ', unsaved edits' : ''}. ${projectName ? `Project “${oneLine(projectName, 80)}”` : 'Personal'}${activeDoc.folder ? ` / ${oneLine(activeDoc.folder, 80)}` : ''}. Id \`${oneLine(activeDoc.id, 80)}\`. ${comments.openCount ? `${comments.openCount} open comment thread${comments.openCount === 1 ? '' : 's'} on this file: doc_comments reads them, doc_comment_reply answers in one. ` : ''}Revise with doc_edit. The user reviews the diff unless document edits are set to accept all.\n\n${fenced(body)}`,
        refs: [{ kind: 'doc', id: activeDoc.id, name: activeDoc.title }],
        hints: ['Summarise this file', 'Tighten the writing', 'Pull out the action items as todos']
      }
    : {
        view: 'docs',
        label: 'Files',
        detail: `No file is open. Files are grouped by project — Personal plus one folder per project. The list shows:\n${lines(docs, (d) => `“${d.title || 'Untitled'}” (\`${d.id}\`)${d.project_id ? ` in project ${d.project_id}` : ' in Personal'}${d.folder ? `/${d.folder}` : ''}`)}`,
        refs: docs.slice(0, 40).map((d) => ({ kind: 'doc', id: d.id, name: d.title })),
        hints: ['What have I been writing about?', 'Start a file for this week’s plan']
      }), [activeDoc?.id, activeDoc?.title, activeDoc?.folder, projectName, body, dirty, docs, comments.openCount])

  return (
    <main className="page docs-page">
      <header className="page-header drag">
        <SidebarToggle />
        {section === 'notes' && <button
          className={`icon-btn no-drag ${treeOpen ? 'on' : ''}`} title={treeOpen ? 'Hide file tree' : 'Show file tree'}
          aria-label="Toggle file tree" aria-pressed={treeOpen} onClick={() => setTreeOpen(!treeOpen)}
        ><FolderTree size={16} /></button>}
        <h2><Files size={16} /> Files</h2>
        <div className="seg no-drag" role="tablist" aria-label="Files section">
          {SECTIONS.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={section === k} className={section === k ? 'on' : ''} onClick={() => openFiles(k)}>{label}</button>
          ))}
        </div>
        <div className="no-drag header-right">
          {section === 'notes' && <NewDocMenu onCreate={(t) => void createDoc(t)} onDaily={() => void openDailyNote()} />}
          {section === 'uploads' && <ScopeSelect value={libraryScope} onChange={(s) => void setLibraryScope(s)} />}
        </div>
        <AppSwitcher />
      </header>

      {section === 'uploads' && <DocumentsView embedded />}
      {section === 'notes' && <div className={`docs-body ${treeOpen ? '' : 'tree-hidden'}`}>
        {/* Both side panels scroll, so their handles live on the body, pinned to the column edges. */}
        {treeOpen && (
          <>
            <aside className="docs-side">
              <DocTree hits={hits} docs={query.trim() ? (matches ?? docs) : docs} activeId={activeDoc?.id ?? null} query={query} onQuery={setQuery} />
            </aside>
            <ResizeHandle id="docs-tree-w" defaultSize={240} min={170} max={480} grows="right" onCollapse={() => setTreeOpen(false)} label="File tree width" className="docs-tree-edge" />
          </>
        )}
        {activeDoc && panelOpen && (
          <ResizeHandle id="docs-history-w" defaultSize={380} min={280} max={720} grows="left" onCollapse={() => setPanel({ ...panel, open: false })} label="Side panel width" className="docs-history-edge" />
        )}

        {!activeDoc ? (
          <section className="empty-state">
            <FileText size={28} />
            <h2>Nothing open</h2>
            <p>{treeOpen ? 'Pick a file on the left, or start a new one.' : 'Show the file tree to pick a file, or start a new one.'}</p>
            <NewDocMenu onCreate={(t) => void createDoc(t)} onDaily={() => void openDailyNote()} />
          </section>
        ) : (
          <section className="docs-main">
            {tabDocs.length > 1 && (
              <div className="doc-tabs">
                {tabDocs.map((d) => (
                  <button key={d.id} className={`doc-tab ${d.id === activeDoc.id ? 'active' : ''}`} onClick={() => void openDoc(d.id)}>
                    <FileText size={11} />{d.title || 'Untitled'}
                    <span className="tab-x" role="button" title="Close tab" aria-label={`Close ${d.title || 'Untitled'}`} onClick={(e) => { e.stopPropagation(); void closeDocTab(d.id) }}><X size={11} /></span>
                  </button>
                ))}
              </div>
            )}

            <div className="doc-toolbar">
              <input
                className="doc-title-input"
                aria-label="Title"
                value={title}
                placeholder="Untitled"
                onChange={(e) => editDocTitle(e.target.value)}
                onBlur={() => void flushDoc()}
                onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }}
              />
              {(docSaving || dirty) && <span className="doc-save-state" title={docSaving ? 'Saving…' : 'Unsaved changes'} aria-label="Unsaved changes" />}
              <span className="spacer" />
              <button className={`ghost-btn xs doc-edit-toggle ${editing ? 'on' : ''}`} title={editing ? 'Back to reading (⌘E)' : 'Edit (⌘E)'} aria-pressed={editing}
                onClick={() => setEditing(!editing)}>
                {editing ? <><BookOpen size={13} /> Read</> : <><Pencil size={13} /> Edit</>}
              </button>
              {editing && <div className="seg">
                <button className={docMode === 'edit' ? 'on' : ''} title="Editor only" onClick={() => setDocMode('edit')}><Pencil size={13} /></button>
                <button className={docMode === 'split' ? 'on' : ''} title="Editor and preview" onClick={() => setDocMode('split')}><Columns2 size={13} /></button>
                <button className={docMode === 'preview' ? 'on' : ''} title="Preview only" onClick={() => setDocMode('preview')}><Eye size={13} /></button>
              </div>}
              {showEditor && (
                <button className="icon-btn ghost" title="Comment on the selected text" aria-label="Comment on the selected text" onClick={() => {
                  const h = editor.current
                  const sel = h?.getSelection()
                  if (h && sel && sel.end > sel.start) commentOn(makeAnchor(h.getText(), sel.start, sel.end))
                }}><MessageSquarePlus size={14} /></button>
              )}
              <ViewMenu icon={<Type size={14} />} title="Font" className="doc-type-menu">
                <TypographyControls value={type} onChange={patchType} onReset={activeDoc.typography ? () => void setDocTypography(activeDoc.id, null) : undefined} />
              </ViewMenu>
              {showEditor && (
                <ViewMenu>
                  <button role="menuitemcheckbox" aria-checked={writeFlags.focus} className="notes-menu-row" title="Dim all but the current paragraph" onClick={() => toggleFlag('focus')}>{writeFlags.focus ? '✓ ' : ''}Focus mode</button>
                  <button role="menuitemcheckbox" aria-checked={writeFlags.typewriter} className="notes-menu-row" title="Keep the caret line mid-height" onClick={() => toggleFlag('typewriter')}>{writeFlags.typewriter ? '✓ ' : ''}Typewriter scrolling</button>
                  {docMode === 'split' && <button role="menuitemcheckbox" aria-checked={linked} className="notes-menu-row" onClick={() => setLinked((l) => !l)}>{linked ? '✓ ' : ''}Link scrolling</button>}
                </ViewMenu>
              )}
              <ExportMenu title={title} content={body} projectId={activeDoc.project_id ?? null} />
              {/* One toggle for the whole panel; the tab strip inside picks what it shows. The dot
                  keeps counting assistant edits waiting for review, whatever tab is open. */}
              <button className={`icon-btn ghost ${panelOpen ? 'on' : ''}`} title={panelOpen ? 'Hide side panel' : 'Show outline, comments, links and history'}
                aria-label="Toggle side panel" aria-pressed={panelOpen} onClick={() => setPanel({ ...panel, open: !panelOpen })}>
                <PanelRight size={14} />
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

            {showEditor && <FormatBar editor={editor} />}
            <div className={`doc-panes ${editing ? docMode : 'read'}`} style={typeStyle}>
              {editing && docMode === 'split' && (
                <ResizeHandle id="doc-split" unit="%" defaultSize={50} min={20} max={80} grows="right" label="Editor and preview split" className="doc-split-edge" />
              )}
              {showEditor && (
                <MarkdownEditor
                  ref={editor}
                  value={body}
                  onChange={editDoc}
                  onSave={() => void flushDoc()}
                  placeholder={'# Title\n\nWrite in markdown. Maths goes in $…$ or $$…$$.'}
                  onScrollFraction={linked && docMode === 'split' ? followEditor : undefined}
                  slash
                  extraCommands={extraCommands}
                  linkTargets={linkTargets}
                  smartPaste
                  imageDocId={activeDoc?.id}
                  richStatus
                  onCaretLine={setCaretLine}
                  focusMode={writeFlags.focus}
                  findFallback
                  typewriter={writeFlags.typewriter}
                />
              )}
              {showRender && (
                <div className={`docs-render markdown ${editing ? '' : 'reading'}`} ref={previewRef} title={editing ? undefined : 'Double-click to edit'}
                  onDoubleClick={() => { if (!editing) setEditing(true) }}>
                  <DocFind scope={previewRef} textRoot={renderRoot} fallback={!showEditor} />
                  {body.trim()
                    ? (
                      <MarkdownPreview
                        source={body}
                        knownTitles={knownTitles}
                        onWikilink={(t) => void openWikilink(t)}
                        onToggleTask={(line) => {
                          const next = toggleTaskAt(body, line)
                          if (next !== null) editDoc(next)
                        }}
                      />
                    )
                    : <p className="muted">{editing ? 'Nothing to preview yet.' : 'Empty file. Press Edit, or double-click here, to write.'}</p>}
                </div>
              )}
            </div>
            <DocCommentFab fab={comments.fab} onComment={() => { if (comments.fab) commentOn(comments.fab.anchor) }} />
          </section>
        )}

        {activeDoc && panelOpen && (
          <aside className="docs-history">
            <header className="docs-panel-tabs" role="tablist">
              {PANEL_TABS.map((t) => (
                <button key={t} role="tab" aria-selected={panel.tab === t} className={`docs-panel-tab ${panel.tab === t ? 'on' : ''}`}
                  onClick={() => setPanel({ open: true, tab: t })}>
                  {PANEL_LABEL[t]}
                  {t === 'history' && pending.length > 0 && <span className="docs-panel-count">{pending.length}</span>}
                  {t === 'comments' && comments.openCount > 0 && <span className="docs-panel-count">{comments.openCount}</span>}
                </button>
              ))}
              <span className="spacer" />
              <button className="icon-btn ghost" title="Close panel" aria-label="Close panel" onClick={() => setPanel({ ...panel, open: false })}><X size={14} /></button>
            </header>

            {panel.tab === 'outline' && <DocOutline source={body} onJump={jumpToLine} activeLine={showEditor ? caretLine : undefined} />}
            {panel.tab === 'comments' && <CommentsPanel state={comments} />}
            {panel.tab === 'links' && <Backlinks docId={activeDoc.id} onOpen={(id) => void openDoc(id)} />}
            {panel.tab === 'history' && (
              <>
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
                    if (confirm('Restore the file to this version? The current text is kept in the history.')) void restoreRevision(r.id)
                  }} />
                ))}
              </>
            )}
          </aside>
        )}
      </div>}
    </main>
  )
}
