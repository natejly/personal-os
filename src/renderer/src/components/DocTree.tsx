import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ChevronRight, FileText, Folder, FolderOpen, FolderPlus, MoreHorizontal, Pin, Plus, Search, Sparkles, Star,
  Trash2, User
} from 'lucide-react'
import type { Doc } from '@shared/types'
import { useStore } from '../store'
import type { DocHit } from '../lib/api'
import { liveDoc } from '../features/docrec/segments'
import {
  buildGroups, canDropDoc, canDropFolder, flattenGroups, folderKey, groupShutKey, joinPath, nameOf,
  parentOf, recentDocs, scopeOf, splitPinned, starredDocs, type Group, type Row, type TreeNode
} from '../lib/docTree'
import { rowButton } from '../lib/rowButton'
import { writeDrag } from '../canvas/dnd'

/**
 * What is being dragged, for the duration of the drag. `dataTransfer` only hands its payload over on
 * drop — during `dragover` a browser will tell you the types but not the values — and the tree has to
 * decide whether a row is a legal target while the pointer is still moving over it.
 */
let dragging:
  | { kind: 'doc'; id: string; scope: string; folder: string }
  | { kind: 'folder'; scope: string; path: string }
  | null = null

const DOC_MIME = 'application/x-grain-doc'
const FOLDER_MIME = 'application/x-grain-folder'

/** Can what is being dragged land here? A group row is its scope's root, which takes anything else. */
const accepts = (scope: string, path: string): boolean => {
  if (!dragging) return false
  return dragging.kind === 'doc'
    ? canDropDoc(dragging.scope, dragging.folder, scope, path)
    : canDropFolder(dragging.path, path, dragging.scope, scope)
}

interface Props {
  hits?: DocHit[] | null
  docs: Doc[]
  activeId: string | null
  query: string
  onQuery: (q: string) => void
}

/**
 * The Files sidebar: Personal and every project as a group of its own, each holding folders nested to
 * any depth, the whole thing expandable and rearranged by dragging — a doc onto a folder files it
 * there, a doc onto another group's row moves it into that project, a folder onto a folder nests it.
 */
/** The snippet with each word that starts like a query word in <mark>, the way the search matched it. */
function markWords(text: string, query: string): React.ReactNode {
  const words = query.match(/\w+/g)
  if (!words) return text
  const re = new RegExp(`(\\b(?:${words.join('|')})\\w*)`, 'gi') // \w+ words need no escaping
  return text.split(re).map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : part))
}

export default function DocTree({ hits, docs, activeId, query, onQuery }: Props): JSX.Element {
  const folders = useStore((s) => s.docFolders)
  const projects = useStore((s) => s.projects)
  const expandedList = useStore((s) => s.expandedFolders)
  const refreshDocFolders = useStore((s) => s.refreshDocFolders)
  const toggleFolder = useStore((s) => s.toggleFolder)
  const expandTo = useStore((s) => s.expandTo)
  const createDocFolder = useStore((s) => s.createDocFolder)
  const renameDocFolder = useStore((s) => s.renameDocFolder)
  const deleteDocFolder = useStore((s) => s.deleteDocFolder)
  const openSettings = useStore((s) => s.openSettings)
  const moveDoc = useStore((s) => s.moveDoc)
  const createDoc = useStore((s) => s.createDoc)
  const recordingDocId = useStore((s) => liveDoc(s.meetingStatus)?.docId ?? '')
  const openDoc = useStore((s) => s.openDoc)
  const deleteDoc = useStore((s) => s.deleteDoc)
  const setDocStar = useStore((s) => s.setDocStar)
  const setDocPin = useStore((s) => s.setDocPin)
  const [topShut, setTopShut] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem('grain.docTopShut') || '[]') } catch { return [] }
  })
  const toggleTop = (k: string): void => {
    const next = topShut.includes(k) ? topShut.filter((x) => x !== k) : [...topShut, k]
    setTopShut(next)
    try { localStorage.setItem('grain.docTopShut', JSON.stringify(next)) } catch { /* storage unavailable */ }
  }

  const [over, setOver] = useState<string | null>(null)
  const [menu, setMenu] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<{ scope: string; path: string; name: string } | null>(null)
  /**
   * A folder being named. It does not exist yet: the row is an input sitting where the folder will
   * go, and nothing is created until the name is committed. Cancel and there is no debris — which is
   * the whole reason this is not "create Untitled, then rename".
   */
  const [draft, setDraft] = useState<{ scope: string; parent: string } | null>(null)
  /**
   * Both naming inputs are uncontrolled and commit on blur, reading the value off the element. Held
   * in state instead, the name a keystroke just set is not yet in the handler's closure when Enter
   * blurs in the same tick, and the folder gets created under the empty name it had a moment ago.
   * Escape has to say so before the blur it causes, hence the ref rather than another state flag.
   */
  const abandon = useRef(false)
  /**
   * Focus for whichever naming input is up. `autoFocus` alone is not enough: these inputs appear in
   * the same commit that unmounts the menu button you opened them from, and the browser's own focus
   * fix-up for the removed button can land after React's. Claiming focus in an effect runs after the
   * whole commit, so there is nothing left to steal it.
   */
  const nameInput = useRef<HTMLInputElement | null>(null)
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => { void refreshDocFolders() }, [refreshDocFolders])
  useEffect(() => {
    if (!draft && !renaming) return
    const el = nameInput.current
    if (!el) return
    el.focus()
    el.select()
  }, [draft, renaming])
  // The open doc's group and folder chain are unfolded, so opening a doc reveals where it lives.
  const active = docs.find((d) => d.id === activeId)
  const activeScope = active ? scopeOf(active) : null
  const activeFolder = active?.folder
  useEffect(() => {
    if (activeScope !== null) expandTo(activeScope, activeFolder ?? '')
  }, [activeScope, activeFolder, expandTo])

  const expanded = useMemo(() => new Set(expandedList), [expandedList])
  const { pinned, rest } = useMemo(() => splitPinned(docs), [docs])
  const starred = useMemo(() => starredDocs(rest), [rest])
  const recent = useMemo(() => recentDocs(rest), [rest])
  const groups = useMemo(() => buildGroups(folders, rest, projects), [folders, rest, projects])
  const rows = useMemo(
    () => flattenGroups(
      groups,
      (scope) => !expanded.has(groupShutKey(scope)),
      (scope, path) => expanded.has(folderKey(scope, path))
    ),
    [groups, expanded]
  )

  const endDrag = (): void => {
    dragging = null
    setOver(null)
    if (hoverTimer.current) clearTimeout(hoverTimer.current)
  }

  /** A row you hover over mid-drag opens by itself, so you can file something two levels down. */
  const hoverOpen = (scope: string, path: string): void => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current)
    const key = path ? folderKey(scope, path) : groupShutKey(scope)
    const alreadyOpen = path ? expanded.has(key) : !expanded.has(key)
    if (alreadyOpen) return
    hoverTimer.current = setTimeout(() => toggleFolder(key), 700)
  }

  const dropOn = (scope: string, path: string) => (e: React.DragEvent): void => {
    e.preventDefault()
    e.stopPropagation()
    const docId = e.dataTransfer.getData(DOC_MIME)
    const folderPath = e.dataTransfer.getData(FOLDER_MIME)
    const from = dragging
    if (docId) void moveDoc(docId, scope, path)
    else if (folderPath && from?.kind === 'folder' && canDropFolder(folderPath, path, from.scope, scope)) {
      void renameDocFolder(folderPath, joinPath(path, nameOf(folderPath)), scope)
    }
    endDrag()
  }

  const dragOver = (scope: string, path: string) => (e: React.DragEvent): void => {
    if (!accepts(scope, path)) return
    e.preventDefault()
    e.stopPropagation()
    e.dataTransfer.dropEffect = 'move'
    const key = folderKey(scope, path)
    if (over !== key) {
      setOver(key)
      hoverOpen(scope, path)
    }
  }

  const newFolder = (scope: string, parent: string): void => {
    setMenu(null)
    expandTo(scope, parent)
    abandon.current = false
    setDraft({ scope, parent })
  }

  /** Escape, or a blank name, means the folder was never made — there is nothing to undo. */
  const commitDraft = (raw: string): void => {
    const name = abandon.current ? '' : raw.trim()
    abandon.current = false
    const d = draft
    setDraft(null)
    if (d && name) void createDocFolder(joinPath(d.parent, name), d.scope)
  }

  const commitRename = (raw: string): void => {
    const name = abandon.current ? '' : raw.trim()
    abandon.current = false
    const r = renaming
    setRenaming(null)
    if (!r || !name) return
    const next = joinPath(parentOf(r.path), name)
    if (next !== r.path) void renameDocFolder(r.path, next, r.scope)
  }

  /** Enter commits, Escape abandons; both leave by blurring, so one handler does the writing. */
  const nameKeys = (e: React.KeyboardEvent<HTMLInputElement>): void => {
    if (e.key === 'Enter') { abandon.current = false; e.currentTarget.blur() }
    if (e.key === 'Escape') { abandon.current = true; e.currentTarget.blur() }
  }

  const removeFolder = (scope: string, path: string, deep: number): void => {
    setMenu(null)
    const msg = deep
      ? `Delete “${nameOf(path)}”? Its ${deep} file${deep === 1 ? '' : 's'} and any subfolders move up one level — nothing is lost.`
      : `Delete “${nameOf(path)}”?`
    if (confirm(msg)) void deleteDocFolder(path, false, scope)
  }

  /** The input that names a folder before it exists. Blank, Escape or a click away, and nothing happened. */
  const draftRow = (depth: number): JSX.Element => (
    <div className="doc-folder-row drafting" style={{ paddingLeft: 6 + depth * 14 }} key="draft">
      <span className="doc-folder-toggle"><ChevronRight size={11} /><Folder size={13} /></span>
      <input
        className="doc-folder-rename"
        autoFocus
        placeholder="Folder name"
        defaultValue=""
        ref={nameInput}
        onBlur={(e) => commitDraft(e.currentTarget.value)}
        onKeyDown={nameKeys}
      />
    </div>
  )

  const docRow = (d: Doc, depth: number, showScope = false): JSX.Element => (
    <div
      key={d.id}
      className={`doc-row ${d.id === activeId ? 'active' : ''}`}
      style={{ paddingLeft: 8 + depth * 14 }}
      {...rowButton(() => void openDoc(d.id))}
      draggable
      onDragStart={(e) => {
        dragging = { kind: 'doc', id: d.id, scope: scopeOf(d), folder: d.folder }
        e.dataTransfer.setData(DOC_MIME, d.id)
        // The same drag also lands on a space (the plane or a sidebar space row) as a doc window.
        writeDrag(e.dataTransfer, { kind: 'doc', id: d.id, label: d.title || 'Untitled', projectId: d.project_id })
        e.dataTransfer.effectAllowed = 'copyMove'
      }}
      onDragEnd={endDrag}
    >
      <FileText size={13} className="doc-row-icon" />
      <span className="doc-row-main">
        <span className="doc-row-title">
          {d.title || 'Untitled'}
          {d.id === recordingDocId && <span className="dr-dot" title="Recording" aria-label="Recording" style={{ display: 'inline-block', marginLeft: 6, background: 'var(--danger)' }} />}
          {typeof d.pending === 'number' && d.pending > 0 && (
            <span className="doc-pending" title={`${d.pending} assistant edit${d.pending === 1 ? '' : 's'} awaiting review`}>
              <Sparkles size={9} />{d.pending}
            </span>
          )}
        </span>
        {showScope && d.folder && <span className="doc-row-meta">{d.folder}</span>}
      </span>
      <button className={`icon-btn ghost xs ${d.pinned ? 'starred' : ''}`} title={d.pinned ? 'Unpin' : 'Pin'}
        onClick={(e) => { e.stopPropagation(); void setDocPin(d.id, !d.pinned) }}>
        <Pin size={12} fill={d.pinned ? 'currentColor' : 'none'} />
      </button>
      <button className={`icon-btn ghost xs ${d.starred ? 'starred' : ''}`} title={d.starred ? 'Unstar' : 'Star'}
        onClick={(e) => { e.stopPropagation(); void setDocStar(d.id, !d.starred) }}>
        <Star size={12} fill={d.starred ? 'currentColor' : 'none'} />
      </button>
      <button className="icon-btn ghost xs danger" title="Delete"
        onClick={(e) => { e.stopPropagation(); void deleteDoc(d.id) }}>
        <Trash2 size={12} />
      </button>
    </div>
  )

  /** A project (or Personal) as the root of its own tree. Always present, even when empty. */
  const groupRow = (g: Group): JSX.Element => {
    const shut = groupShutKey(g.scope)
    const open = !expanded.has(shut)
    const key = folderKey(g.scope, '')
    const menuKey = `g:${g.scope}`
    return (
      <div
        key={menuKey}
        className={`doc-group-row ${over === key ? 'drop-into' : ''} ${menu === menuKey ? 'menu-open' : ''}`}
        onDragOver={dragOver(g.scope, '')}
        onDragLeave={() => { if (over === key) setOver(null) }}
        onDrop={dropOn(g.scope, '')}
      >
        <button className="doc-folder-toggle" onClick={() => toggleFolder(shut)} aria-expanded={open}
          aria-label={`${open ? 'Collapse' : 'Expand'} ${g.name}`}>
          <ChevronRight size={11} className={open ? 'rot90' : undefined} />
          {g.scope
            ? <span className="project-dot" style={g.color ? { background: g.color } : undefined} />
            : <User size={12} />}
        </button>
        <button className="doc-group-name" onClick={() => toggleFolder(shut)} title={g.orphan ? 'This project no longer exists; its files are personal now' : g.name}>
          {g.name}
        </button>
        <button className="icon-btn ghost xs" title={`New file in ${g.name}`} aria-label={`New file in ${g.name}`}
          onClick={(e) => { e.stopPropagation(); void createDoc({ project_id: g.scope || null }) }}>
          <Plus size={13} />
        </button>
        <button className="icon-btn ghost xs" title="Actions" aria-label={`Actions for ${g.name}`}
          onClick={(e) => { e.stopPropagation(); setMenu(menu === menuKey ? null : menuKey) }}>
          <MoreHorizontal size={13} />
        </button>
        {menu === menuKey && (
          <div className="doc-folder-menu" onMouseDown={(e) => e.stopPropagation()}>
            <button onClick={() => { setMenu(null); void createDoc({ project_id: g.scope || null }) }}>New file here</button>
            <button onClick={() => newFolder(g.scope, '')}>New folder…</button>
          </div>
        )}
      </div>
    )
  }

  const folderRow = (f: TreeNode, depth: number): JSX.Element => {
    const key = folderKey(f.scope, f.path)
    const open = expanded.has(key)
    return (
      <div
        key={`f:${key}`}
        className={`doc-folder-row ${over === key ? 'drop-into' : ''} ${menu === key ? 'menu-open' : ''}`}
        style={{ paddingLeft: 6 + depth * 14 }}
        onDragOver={dragOver(f.scope, f.path)}
        onDragLeave={() => { if (over === key) setOver(null) }}
        onDrop={dropOn(f.scope, f.path)}
        draggable={!renaming && !draft}
        onDragStart={(e) => {
          dragging = { kind: 'folder', scope: f.scope, path: f.path }
          e.dataTransfer.effectAllowed = 'move'
          e.dataTransfer.setData(FOLDER_MIME, f.path)
          e.dataTransfer.setData('text/plain', f.path)
        }}
        onDragEnd={endDrag}
      >
        <button className="doc-folder-toggle" onClick={() => toggleFolder(key)} aria-expanded={open}
          aria-label={`${open ? 'Collapse' : 'Expand'} ${f.name}`}>
          <ChevronRight size={11} className={open ? 'rot90' : undefined} />
          {open ? <FolderOpen size={13} /> : <Folder size={13} />}
        </button>
        {renaming?.path === f.path && renaming.scope === f.scope ? (
          <input
            className="doc-folder-rename"
            autoFocus
            defaultValue={renaming.name}
            ref={nameInput}
            onBlur={(e) => commitRename(e.currentTarget.value)}
            onKeyDown={nameKeys}
          />
        ) : (
          <button className="doc-folder-name" onClick={() => toggleFolder(key)}
            onDoubleClick={() => setRenaming({ scope: f.scope, path: f.path, name: f.name })}>
            {f.name}
          </button>
        )}
        <button className="icon-btn ghost xs" title="Folder actions" aria-label={`Actions for ${f.name}`}
          onClick={(e) => { e.stopPropagation(); setMenu(menu === key ? null : key) }}>
          <MoreHorizontal size={13} />
        </button>
        {menu === key && (
          <div className="doc-folder-menu" onMouseDown={(e) => e.stopPropagation()}>
            <button onClick={() => { setMenu(null); void createDoc({ project_id: f.scope || null, folder: f.path }) }}>New file here</button>
            <button onClick={() => newFolder(f.scope, f.path)}>New subfolder…</button>
            <button onClick={() => { setMenu(null); setRenaming({ scope: f.scope, path: f.path, name: f.name }) }}>Rename</button>
            <button className="danger" onClick={() => removeFolder(f.scope, f.path, f.deep)}>Delete folder</button>
          </div>
        )}
      </div>
    )
  }

  /** A headed, collapsible shortcut list above the folder groups; rows are the same as the tree's. */
  const topSection = (key: string, title: string, list: Doc[]): JSX.Element | null => {
    if (!list.length) return null
    const open = !topShut.includes(key)
    return (
      <div key={`top:${key}`}>
        <div className="doc-group-row">
          <button className="doc-folder-toggle" onClick={() => toggleTop(key)} aria-expanded={open}
            aria-label={`${open ? 'Collapse' : 'Expand'} ${title}`}>
            <ChevronRight size={11} className={open ? 'rot90' : undefined} />
          </button>
          <button className="doc-group-name" onClick={() => toggleTop(key)}>{title}</button>
        </div>
        {open && list.map((d) => docRow(d, 1, true))}
      </div>
    )
  }

  const searching = query.trim().length > 0
  // The chips come from the unfiltered list; while a #tag is searched `docs` is already narrowed, so keep the last full set.
  const allTags = useRef<string[]>([])
  if (!searching) allTags.current = [...new Set(docs.flatMap((d) => d.tags ?? []))].sort()

  /** The draft row belongs directly under the row it was started from, at its children's indent. */
  const render = (row: Row): JSX.Element[] => {
    const out: JSX.Element[] = []
    if (row.kind === 'group') out.push(groupRow(row.group as Group))
    else if (row.kind === 'folder') out.push(folderRow(row.folder as TreeNode, row.depth))
    else out.push(docRow(row.doc as Doc, row.depth))
    const under = row.kind === 'group' ? '' : row.kind === 'folder' ? (row.folder as TreeNode).path : null
    if (draft && under !== null && draft.scope === row.scope && draft.parent === under) {
      out.push(draftRow(row.depth + 1))
    }
    return out
  }

  return (
    <div className="doc-tree" onMouseDown={() => menu && setMenu(null)}>
      <div className="doc-tree-head">
        <label className="search mini"><Search size={12} /><input placeholder="Search files" value={query} onChange={(e) => onQuery(e.target.value)} /></label>
        <button className="icon-btn ghost" title="New folder in Personal" aria-label="New folder in Personal"
          onClick={() => newFolder('', '')}><FolderPlus size={14} /></button>
      </div>

      {allTags.current.length > 0 && (
        <div className="doc-tag-chips">
          {allTags.current.map((t) => (
            <button key={t} className={`doc-tag-chip${query.trim().toLowerCase() === '#' + t ? ' on' : ''}`}
              onClick={() => onQuery(query.trim().toLowerCase() === '#' + t ? '' : '#' + t)}>#{t}</button>
          ))}
        </div>
      )}

      {/* A search is a flat answer, not a shape: matches come back wherever they are filed. */}
      {searching
        ? (hits ? (hits.length === 0 ? <p className="empty-hint">No matches.</p> : hits.map((h) => (
          <div key={h.doc_id} className={`doc-row ${h.doc_id === activeId ? 'active' : ''}`} role="button" tabIndex={0}
            onClick={() => void openDoc(h.doc_id)}>
            <FileText size={13} className="doc-row-icon" />
            <span className="doc-row-main">
              <span className="doc-row-title">{h.title || 'Untitled'}{h.via === 'recording' && <span className="doc-row-meta"> · heard in a recording</span>}</span>
              <span className="doc-row-meta doc-hit-snippet">{markWords(h.snippet, query)}</span>
            </span>
          </div>
        )))
        : docs.length === 0 ? <p className="empty-hint">No matches.</p> : docs.map((d) => docRow(d, 0, true)))
        : <>
          {topSection('pinned', 'Pinned', pinned)}
          {topSection('starred', 'Starred', starred)}
          {topSection('recent', 'Recent', recent)}
          {rows.flatMap(render)}
        </>}

      {!searching && <div className="doc-tree-tail" aria-hidden />}
      {!searching && (
        <div className="doc-row" role="button" tabIndex={0} onClick={() => openSettings('data')}
          onKeyDown={(e) => { if (e.key === 'Enter') openSettings('data') }}>
          <Trash2 size={13} className="doc-row-icon" />
          <span className="doc-row-main"><span className="doc-row-title">Trash</span></span>
        </div>
      )}
    </div>
  )
}
