import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  FileText, Files, PanelLeftOpen, PanelRight, X, Columns2, Eye, Pencil,
  Sparkles, Save, Link2, Link2Off, ChevronDown, Folder, FolderKanban, FolderTree, Focus, AlignVerticalSpaceAround
} from 'lucide-react'
import { useStore } from '../store'
import { api, type DocHit } from '../lib/api'
import type { Doc } from '@shared/types'
import MarkdownEditor from './MarkdownEditor'
import MarkdownPreview from './MarkdownPreview'
import DiffView from './DiffView'
import DocTree from './DocTree'
import { scopeOf } from '../lib/docTree'
import ResizeHandle from './ResizeHandle'
import { oneLine } from '../lib/emailAsk'
import { fenced, lines, usePageContext } from '../lib/pageContext'
import { PANEL_TABS, parsePanelState, resolveWikiDoc, type PanelState, type PanelTab } from '../lib/docPanel'
import { DocRecordButton, DocRecorderBar, liveDoc, RecordingsPanel, setRecordingBlockSink, useDictation, useDocRec, useNoteMarks, usePreview } from '../features/docrec'
import { useDictationChord } from '../features/docrec/useChord'
import Backlinks from '../features/notes/Backlinks'
import DocOutline from '../features/notes/DocOutline'
import FormatBar from '../features/notes/FormatBar'
import ExportMenu from '../features/notes/ExportMenu'
import { expandTemplate, userTemplates } from '../features/notes/templates'
import NewDocMenu from '../features/notes/NewDocMenu'
import type { MarkdownEditorHandle } from '../features/notes/handle'
import type { SlashCommand } from '../features/notes/slash'
import { toggleTaskAt } from '../features/notes/tasks'
import '../styles/docs.css'
import AppSwitcher from './AppSwitcher'

const PANEL_KEY = 'grain.docs.panel'
const readPanel = (): PanelState => {
  try { return parsePanelState(localStorage.getItem(PANEL_KEY)) } catch { return parsePanelState(null) }
}
const PANEL_LABEL: Record<PanelTab, string> = { outline: 'Outline', recordings: 'Recordings', links: 'Links', history: 'History' }

const TREE_KEY = 'grain.docs.treeOpen'
const treeWasOpen = (): boolean => {
  try { return localStorage.getItem(TREE_KEY) !== '0' } catch { return true }
}

const flagOn = (key: string): boolean => {
  try { return localStorage.getItem(key) === '1' } catch { return false }
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
  const docFocusId = useStore((s) => s.docFocusId)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const {
    refreshDocs, openDoc, closeDocTab, createDoc, editDoc, editDocTitle, flushDoc, moveDoc,
    setDocMode, acceptRevision, rejectRevision, restoreRevision, toggleSidebar, openDailyNote
  } = useStore()
  // Select the status itself, not `liveDoc(status)`: that builds a new object on every call, and a
  // selector whose result is never identical re-renders forever the moment a recording is live.
  const meetingStatus = useStore((s) => s.meetingStatus)
  const live = useMemo(() => liveDoc(meetingStatus), [meetingStatus])

  const [query, setQuery] = useState('')
  // One tabbed panel in the right-hand column (Outline, Recordings, Links, History).
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
  const [editFrac, setEditFrac] = useState<number | null>(null)
  // Non-null while "New folder…" is being typed in the toolbar. An Electron renderer has no
  // window.prompt, so the picker turns into a text input in place rather than asking in a dialog.
  const [folderDraft, setFolderDraft] = useState<string | null>(null)
  const previewRef = useRef<HTMLDivElement>(null)

  useEffect(() => { void refreshDocs() }, [refreshDocs])
  // The narrowed list lives here, not in the store: tabs, folders, links and templates read the full `docs`.
  const [matches, setMatches] = useState<Doc[] | null>(null)
  useEffect(() => {
    setMatches(null)
    const q = query.trim()
    if (!q) return
    let stale = false
    api.docs.list('all', q).then((d) => { if (!stale) setMatches(d) }).catch(() => { /* keep the last answer */ })
    return () => { stale = true }
  }, [query])
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
  useNoteMarks(activeDoc?.id ?? '', body)
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

  // A doc just made by New or Today's note gets the caret, at its end, so typing can start at once.
  useEffect(() => {
    if (!docFocusId || docFocusId !== activeDoc?.id) return
    if (docMode !== 'preview') editor.current?.jumpToLine(Number.MAX_SAFE_INTEGER)
    useStore.setState({ docFocusId: null })
  }, [docFocusId, activeDoc?.id, docMode])

  // Linked scrolling: the preview follows the editor's fraction of the way down.
  useEffect(() => {
    const el = previewRef.current
    if (!linked || el == null || editFrac == null) return
    const range = el.scrollHeight - el.clientHeight
    if (range > 0) el.scrollTop = editFrac * range
  }, [editFrac, linked])

  const dirty = (docDraft !== null && docDraft !== activeDoc?.content) ||
    (docTitleDraft !== null && docTitleDraft !== activeDoc?.title)

  const docId = activeDoc?.id ?? ''
  const liveHere = live && live.docId === docId ? live : null
  const recs = useDocRec((s) => s.recordings[docId])
  const recordingCount = recs?.length ?? 0
  // Ids the agent can hand to meeting_read, which also answers mid-recording from the transcript so far.
  const recList = (recs ?? []).slice(0, 5).map((r) => `\`${r.id}\` ${r.title || 'Untitled'} (${r.status}, summary ${r.summary_state})`).join('; ')

  // ---- editor wiring ----
  // Slash-menu entries that need the recorder; the editor's own commands are built in. Memoised
  // because the editor re-derives its command list from this array's identity.
  const userTpls = useMemo(() => userTemplates(docs), [docs])
  const extraCommands = useMemo((): SlashCommand[] => (docId
    ? ([
        { id: 'record', label: 'Record and summarize', hint: 'mic', keywords: ['record', 'transcribe', 'meeting', 'summary'], run: () => void useDocRec.getState().start(docId, 'record') },
        { id: 'dictate', label: 'Dictate into note', hint: 'mic', keywords: ['dictate', 'speak', 'voice', 'talk'], run: () => void useDocRec.getState().start(docId, 'dictate') },
        { id: 'daily', label: 'Daily note', hint: 'today', keywords: ['today', 'journal', 'daily'], run: () => void openDailyNote() }
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
  // Starting a recording writes its block at the caret, through the editor's own undo-safe insert.
  useEffect(() => {
    setRecordingBlockSink((id, make) => {
      const h = editor.current
      if (id !== docId || !h) return
      const t = h.getText()
      const { start, end } = h.getSelection()
      h.insertQuietly(make(t.slice(0, start), t.slice(end)))
    })
    return () => setRecordingBlockSink(null)
  }, [docId])
  // A link can point at any other titled doc. The list may be narrowed by the tree's search box; that
  // only shortens the picker.
  const linkTargets = useMemo(
    () => docs.filter((d) => d.id !== docId && d.title.trim()).map((d) => ({ id: d.id, title: d.title })),
    [docs, docId]
  )
  const knownTitles = useMemo(() => new Set(docs.map((d) => d.title).filter((t) => t.trim())), [docs])

  // Dictated clips go through the editor's own insert, which fires a real input event, so they take
  // the same editDoc autosave path as typing.
  useDictation(
    docId,
    // Never focuses: the user may have clicked into the chat or the title since the clip was said.
    (text) => editor.current?.insertQuietly(text) ?? false,
    () => {
      const h = editor.current
      if (!h) return ''
      const { start } = h.getSelection()
      return h.getText().slice(Math.max(0, start - 80), start)
    },
    // "scratch that": range-checked, so text typed since the clip is never eaten.
    (text) => {
      const h = editor.current
      if (!h || !text) return
      const { start } = h.getSelection()
      if (h.getText().slice(0, start).endsWith(text)) h.replaceRange(start - text.length, start, '')
    }
  )

  // Dictation types into the editor, so a preview-only view has nowhere to put the words.
  const dictatingHere = liveHere?.mode === 'dictate'
  const previewText = usePreview((s) => (dictatingHere && liveHere ? s.byId[liveHere.meetingId]?.text ?? '' : ''))
  // Hold-to-talk. Not while an assistant revision waits for review: the words would land under a diff.
  useDictationChord(docId, pending.length === 0, dictatingHere)
  useEffect(() => { if (dictatingHere && docMode === 'preview') setDocMode('split') }, [dictatingHere, docMode, setDocMode])

  // A recording that starts on THIS doc opens the Recordings tab, so the live transcript is in view.
  // Seeded per doc, so merely opening a doc that is already recording does not move the panel.
  const liveSeen = useRef({ doc: '', id: '' })
  const liveId = liveHere?.meetingId ?? ''
  const liveMode = liveHere?.mode
  useEffect(() => {
    const prev = liveSeen.current
    liveSeen.current = { doc: docId, id: liveId }
    if (liveId && prev.doc === docId && prev.id !== liveId && liveMode === 'record') setPanel({ open: true, tab: 'recordings' })
  }, [docId, liveId, liveMode, setPanel])

  // An outline jump from preview-only mode switches to the split view first; the editor mounts on the
  // next render, so the jump waits for it.
  const jumpToLine = (line: number): void => {
    if (docMode === 'preview') {
      pendingJump.current = line
      setDocMode('split')
    } else editor.current?.jumpToLine(line)
  }
  useEffect(() => {
    if (docMode === 'preview' || pendingJump.current === null) return
    editor.current?.jumpToLine(pendingJump.current)
    pendingJump.current = null
  }, [docMode])

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
        label: `Doc “${oneLine(activeDoc.title || 'Untitled', 80)}”`,
        detail: `Open${dirty ? ', unsaved edits' : ''}. ${projectName ? `Project “${oneLine(projectName, 80)}”` : 'Personal'}${activeDoc.folder ? ` / ${oneLine(activeDoc.folder, 80)}` : ''}. Id \`${oneLine(activeDoc.id, 80)}\`. ${liveHere ? `Being ${liveHere.mode === 'dictate' ? 'dictated into' : 'recorded'} now (recording \`${oneLine(liveHere.meetingId, 80)}\`). ` : ''}${recordingCount ? `${recordingCount} recording${recordingCount === 1 ? '' : 's'} linked to this doc${recList ? `: ${oneLine(recList, 600)}` : ''}. ` : ''}Revise with doc_edit. The user reviews the diff unless document edits are set to accept all.\n\n${fenced(body)}`,
        refs: [{ kind: 'doc', id: activeDoc.id, name: activeDoc.title }],
        hints: ['Summarise this doc', 'Tighten the writing', 'Pull out the action items as todos']
      }
    : {
        view: 'docs',
        label: 'Files',
        detail: `No doc is open. Files are grouped by project — Personal plus one folder per project. The list shows:\n${lines(docs, (d) => `“${d.title || 'Untitled'}” (\`${d.id}\`)${d.project_id ? ` in project ${d.project_id}` : ' in Personal'}${d.folder ? `/${d.folder}` : ''}`)}`,
        refs: docs.slice(0, 40).map((d) => ({ kind: 'doc', id: d.id, name: d.title })),
        hints: ['What have I been writing about?', 'Start a doc for this week’s plan']
      }), [activeDoc?.id, activeDoc?.title, activeDoc?.folder, projectName, body, dirty, docs, liveHere?.meetingId, liveHere?.mode, recordingCount, recList])

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
          <NewDocMenu onCreate={(t) => void createDoc(t)} onDaily={() => void openDailyNote()} />
        </div>
        <AppSwitcher />
      </header>

      <div className={`docs-body ${treeOpen ? '' : 'tree-hidden'}`}>
        {/* Both side panels scroll, so their handles live on the body, pinned to the column edges. */}
        {treeOpen && (
          <>
            <aside className="docs-side">
              <DocTree hits={hits} docs={query.trim() ? (matches ?? []) : docs} activeId={activeDoc?.id ?? null} query={query} onQuery={setQuery} />
            </aside>
            <ResizeHandle id="docs-tree-w" defaultSize={240} min={170} max={480} grows="right" onCollapse={() => setTreeOpen(false)} label="File tree width" className="docs-tree-edge" />
          </>
        )}
        {activeDoc && panelOpen && (
          <ResizeHandle id="docs-history-w" defaultSize={380} min={280} max={720} grows="left" onCollapse={() => setPanel({ ...panel, open: false })} label="Side panel width" className="docs-history-edge" />
        )}

        {!activeDoc ? (
          <section className="docs-empty">
            <FileText size={30} />
            <h2>Nothing open</h2>
            <p className="muted">Pick a file on the left, or start a new one.</p>
            <NewDocMenu onCreate={(t) => void createDoc(t)} onDaily={() => void openDailyNote()} />
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
              {docMode !== 'preview' && (
                <>
                  <button className={`icon-btn ghost ${writeFlags.focus ? 'on' : ''}`} title="Focus mode: dim all but the current paragraph" aria-pressed={writeFlags.focus} onClick={() => toggleFlag('focus')}><Focus size={14} /></button>
                  <button className={`icon-btn ghost ${writeFlags.typewriter ? 'on' : ''}`} title="Typewriter scrolling: keep the caret line mid-height" aria-pressed={writeFlags.typewriter} onClick={() => toggleFlag('typewriter')}><AlignVerticalSpaceAround size={14} /></button>
                </>
              )}
              {docMode === 'split' && (
                <button className="icon-btn ghost" title={linked ? 'Unlink scrolling' : 'Link scrolling'} onClick={() => setLinked((l) => !l)}>
                  {linked ? <Link2 size={14} /> : <Link2Off size={14} />}
                </button>
              )}
              <button className="icon-btn ghost" title="Save now (⌘S) — it autosaves anyway" onClick={() => void flushDoc()}><Save size={14} /></button>
              <DocRecordButton docId={activeDoc.id} />
              <ExportMenu title={title} content={body} />
              {/* One toggle for the whole panel; the tab strip inside picks what it shows. The dot
                  keeps counting assistant edits waiting for review, whatever tab is open. */}
              <button className={`icon-btn ghost ${panelOpen ? 'on' : ''}`} title={panelOpen ? 'Hide side panel' : 'Show outline, recordings, links and history'}
                aria-label="Toggle side panel" aria-pressed={panelOpen} onClick={() => setPanel({ ...panel, open: !panelOpen })}>
                <PanelRight size={14} />
                {pending.length > 0 && <span className="dot-badge">{pending.length}</span>}
              </button>
            </div>

            <DocRecorderBar docId={activeDoc.id} />

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

            {docMode !== 'preview' && <FormatBar editor={editor} />}
            <div className={`doc-panes ${docMode}`}>
              {docMode === 'split' && (
                <ResizeHandle id="doc-split" unit="%" defaultSize={50} min={20} max={80} grows="right" label="Editor and preview split" className="doc-split-edge" />
              )}
              {docMode !== 'preview' && (
                <MarkdownEditor
                  ref={editor}
                  value={body}
                  onChange={editDoc}
                  onSave={() => void flushDoc()}
                  placeholder={'# Title\n\nWrite in markdown. Maths goes in $…$ or $$…$$.'}
                  onScrollFraction={linked && docMode === 'split' ? setEditFrac : undefined}
                  slash
                  extraCommands={extraCommands}
                  linkTargets={linkTargets}
                  smartPaste
                  imageDocId={activeDoc?.id}
                  richStatus
                  previewText={previewText}
                  onCaretLine={setCaretLine}
                  focusMode={writeFlags.focus}
                  typewriter={writeFlags.typewriter}
                />
              )}
              {docMode !== 'edit' && (
                <div className="docs-render markdown" ref={previewRef}>
                  {body.trim()
                    ? (
                      <MarkdownPreview
                        source={body}
                        knownTitles={knownTitles}
                        onWikilink={(t) => void openWikilink(t)}
                        onRecording={(id) => { setPanel({ open: true, tab: 'recordings' }); void useDocRec.getState().select(docId, id) }}
                        onToggleTask={(line) => {
                          const next = toggleTaskAt(body, line)
                          if (next !== null) editDoc(next)
                        }}
                      />
                    )
                    : <p className="muted">Nothing to preview yet.</p>}
                </div>
              )}
            </div>
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
                  {t === 'recordings' && liveHere && <span className="docs-panel-live" aria-label="Recording" />}
                </button>
              ))}
              <span className="spacer" />
              <button className="icon-btn ghost" title="Close panel" aria-label="Close panel" onClick={() => setPanel({ ...panel, open: false })}><X size={14} /></button>
            </header>

            {panel.tab === 'outline' && <DocOutline source={body} onJump={jumpToLine} activeLine={docMode === 'preview' ? undefined : caretLine} />}
            {panel.tab === 'recordings' && <RecordingsPanel docId={activeDoc.id} />}
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
                    if (confirm('Restore the document to this version? The current text is kept in the history.')) void restoreRevision(r.id)
                  }} />
                ))}
              </>
            )}
          </aside>
        )}
      </div>
    </main>
  )
}
