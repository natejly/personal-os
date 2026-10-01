import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ChevronRight, FileText, Folder, FolderOpen, FolderPlus, MoreHorizontal, Search, Sparkles, Star, Trash2
} from 'lucide-react'
import type { Doc } from '@shared/types'
import { useStore } from '../store'
import { buildTree, canDropFolder, flatten, joinPath, nameOf, parentOf, type Row } from '../lib/docTree'
import ProjectChip from './ProjectChip'

/**
 * What is being dragged, for the duration of the drag. `dataTransfer` only hands its payload over on
 * drop — during `dragover` a browser will tell you the types but not the values — and the tree has to
 * decide whether a row is a legal target while the pointer is still moving over it.
 */
let dragging: { kind: 'doc'; id: string; folder: string } | { kind: 'folder'; path: string } | null = null

const DOC_MIME = 'application/x-grain-doc'
const FOLDER_MIME = 'application/x-grain-folder'

const fmtWhen = (ts: number): string => {
  const d = new Date(ts * 1000)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return d.getTime() >= today.getTime()
    ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    : d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

/** Can what is being dragged land on this folder? '' is the root, which takes anything filed deeper. */
const accepts = (dest: string): boolean => {
  if (!dragging) return false
  return dragging.kind === 'doc' ? dragging.folder !== dest : canDropFolder(dragging.path, dest)
}

interface Props {
  docs: Doc[]
  activeId: string | null
  query: string
  onQuery: (q: string) => void
  /** The library is showing every scope, so each row says which project it belongs to. */
  showScope: boolean
  /** Where a new doc or folder made from the tree's own buttons should land. */
  projectId: string | null
}

/**
 * The Docs sidebar: folders nested to any depth, each holding docs, the whole thing expandable and
 * rearranged by dragging — a doc onto a folder files it there, a folder onto a folder nests it.
 */
export default function DocTree({ docs, activeId, query, onQuery, showScope, projectId }: Props): JSX.Element {
  const folders = useStore((s) => s.docFolders)
  const expandedList = useStore((s) => s.expandedFolders)
  const refreshDocFolders = useStore((s) => s.refreshDocFolders)
  const toggleFolder = useStore((s) => s.toggleFolder)
  const expandTo = useStore((s) => s.expandTo)
  const createDocFolder = useStore((s) => s.createDocFolder)
  const renameDocFolder = useStore((s) => s.renameDocFolder)
  const deleteDocFolder = useStore((s) => s.deleteDocFolder)
  const setDocFolder = useStore((s) => s.setDocFolder)
  const createDoc = useStore((s) => s.createDoc)
  const openDoc = useStore((s) => s.openDoc)
  const deleteDoc = useStore((s) => s.deleteDoc)
  const setDocStar = useStore((s) => s.setDocStar)

  const [over, setOver] = useState<string | null>(null)
  const [menu, setMenu] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<{ path: string; draft: string } | null>(null)
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => { void refreshDocFolders() }, [refreshDocFolders])
  // The open doc's folder chain is unfolded, so opening a doc from anywhere reveals where it lives.
  const activeFolder = docs.find((d) => d.id === activeId)?.folder
  useEffect(() => { if (activeFolder) expandTo(activeFolder) }, [activeFolder, expandTo])

  const expanded = useMemo(() => new Set(expandedList), [expandedList])
  const tree = useMemo(() => buildTree(folders, docs), [folders, docs])
  const rows = useMemo(() => flatten(tree, (p) => expanded.has(p)), [tree, expanded])

  const endDrag = (): void => {
    dragging = null
    setOver(null)
    if (hoverTimer.current) clearTimeout(hoverTimer.current)
  }

  /** A folder you hover over mid-drag opens by itself, so you can file something two levels down. */
  const hoverOpen = (path: string): void => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current)
    if (!path || expanded.has(path)) return
    hoverTimer.current = setTimeout(() => toggleFolder(path), 700)
  }

  const dropOn = (dest: string) => (e: React.DragEvent): void => {
    e.preventDefault()
    e.stopPropagation()
    const docId = e.dataTransfer.getData(DOC_MIME)
    const folderPath = e.dataTransfer.getData(FOLDER_MIME)
    if (docId) void setDocFolder(docId, dest)
    else if (folderPath && canDropFolder(folderPath, dest)) void renameDocFolder(folderPath, joinPath(dest, nameOf(folderPath)))
    endDrag()
  }

  const dragOver = (dest: string) => (e: React.DragEvent): void => {
    if (!accepts(dest)) return
    e.preventDefault()
    e.stopPropagation()
    e.dataTransfer.dropEffect = 'move'
    if (over !== dest) {
      setOver(dest)
      hoverOpen(dest)
    }
  }

  const newFolder = (parent: string): void => {
    const name = prompt(parent ? `New folder inside “${parent}”` : 'New folder')?.trim()
    if (name) void createDocFolder(joinPath(parent, name))
    setMenu(null)
  }

  const commitRename = (): void => {
    if (!renaming) return
    const name = renaming.draft.trim()
    const next = joinPath(parentOf(renaming.path), name)
    if (name && next !== renaming.path) void renameDocFolder(renaming.path, next)
    setRenaming(null)
  }

  const removeFolder = (path: string, deep: number): void => {
    setMenu(null)
    const msg = deep
      ? `Delete “${nameOf(path)}”? Its ${deep} doc${deep === 1 ? '' : 's'} move up to the folder above — nothing is lost.`
      : `Delete “${nameOf(path)}”?`
    if (confirm(msg)) void deleteDocFolder(path, false)
  }

  const docRow = (d: Doc, depth: number): JSX.Element => (
    <div
      key={d.id}
      className={`doc-row ${d.id === activeId ? 'active' : ''}`}
      style={{ paddingLeft: 8 + depth * 14 }}
      onClick={() => void openDoc(d.id)}
      role="button"
      tabIndex={0}
      draggable
      onDragStart={(e) => {
        dragging = { kind: 'doc', id: d.id, folder: d.folder }
        e.dataTransfer.effectAllowed = 'move'
        e.dataTransfer.setData(DOC_MIME, d.id)
        e.dataTransfer.setData('text/plain', d.title)
      }}
      onDragEnd={endDrag}
    >
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
          {query && d.folder ? `${d.folder} · ` : ''}{d.words} words · {fmtWhen(d.updated_at)}
        </span>
      </span>
      <button className={`icon-btn ghost xs ${d.starred ? 'starred' : ''}`} title={d.starred ? 'Unstar' : 'Star'}
        onClick={(e) => { e.stopPropagation(); void setDocStar(d.id, !d.starred) }}>
        <Star size={12} fill={d.starred ? 'currentColor' : 'none'} />
      </button>
      <button className="icon-btn ghost xs danger" title="Delete"
        onClick={(e) => { e.stopPropagation(); if (confirm(`Delete “${d.title}”? Its revision history goes too.`)) void deleteDoc(d.id) }}>
        <Trash2 size={12} />
      </button>
    </div>
  )

  const folderRow = (row: Row): JSX.Element => {
    const f = row.folder as NonNullable<Row['folder']>
    const open = expanded.has(f.path)
    return (
      <div
        key={`f:${f.path}`}
        className={`doc-folder-row ${over === f.path ? 'drop-into' : ''} ${menu === f.path ? 'menu-open' : ''}`}
        style={{ paddingLeft: 6 + row.depth * 14 }}
        onDragOver={dragOver(f.path)}
        onDragLeave={() => { if (over === f.path) setOver(null) }}
        onDrop={dropOn(f.path)}
        draggable={!renaming}
        onDragStart={(e) => {
          dragging = { kind: 'folder', path: f.path }
          e.dataTransfer.effectAllowed = 'move'
          e.dataTransfer.setData(FOLDER_MIME, f.path)
          e.dataTransfer.setData('text/plain', f.path)
        }}
        onDragEnd={endDrag}
      >
        <button className="doc-folder-toggle" onClick={() => toggleFolder(f.path)} aria-expanded={open}
          aria-label={`${open ? 'Collapse' : 'Expand'} ${f.name}`}>
          <ChevronRight size={11} className={open ? 'rot90' : undefined} />
          {open ? <FolderOpen size={13} /> : <Folder size={13} />}
        </button>
        {renaming?.path === f.path ? (
          <input
            className="doc-folder-rename"
            autoFocus
            value={renaming.draft}
            onChange={(e) => setRenaming({ path: f.path, draft: e.target.value })}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === 'Enter') e.currentTarget.blur()
              if (e.key === 'Escape') setRenaming(null)
            }}
          />
        ) : (
          <button className="doc-folder-name" onClick={() => toggleFolder(f.path)} onDoubleClick={() => setRenaming({ path: f.path, draft: f.name })}>
            {f.name}
          </button>
        )}
        <span className="count">{f.deep || ''}</span>
        <button className="icon-btn ghost xs" title="Folder actions" aria-label={`Actions for ${f.name}`}
          onClick={(e) => { e.stopPropagation(); setMenu(menu === f.path ? null : f.path) }}>
          <MoreHorizontal size={13} />
        </button>
        {menu === f.path && (
          <div className="doc-folder-menu" onMouseDown={(e) => e.stopPropagation()}>
            <button onClick={() => { setMenu(null); void createDoc({ project_id: projectId, folder: f.path }) }}>New doc here</button>
            <button onClick={() => newFolder(f.path)}>New subfolder…</button>
            <button onClick={() => { setMenu(null); setRenaming({ path: f.path, draft: f.name }) }}>Rename</button>
            <button className="danger" onClick={() => removeFolder(f.path, f.deep)}>Delete folder</button>
          </div>
        )}
      </div>
    )
  }

  const searching = query.trim().length > 0

  return (
    <div
      className={`doc-tree ${over === '' ? 'drop-root' : ''}`}
      onDragOver={dragOver('')}
      onDragLeave={() => { if (over === '') setOver(null) }}
      onDrop={dropOn('')}
      onMouseDown={() => menu && setMenu(null)}
    >
      <div className="doc-tree-head">
        <label className="search mini"><Search size={12} /><input placeholder="Search docs" value={query} onChange={(e) => onQuery(e.target.value)} /></label>
        <button className="icon-btn ghost" title="New folder" aria-label="New folder" onClick={() => newFolder('')}><FolderPlus size={14} /></button>
      </div>

      {docs.length === 0 && folders.length === 0 && <p className="empty-hint">{searching ? 'No matches.' : 'No docs yet.'}</p>}

      {/* A search is a flat answer, not a shape: matches come back wherever they are filed. */}
      {searching
        ? (docs.length === 0 ? <p className="empty-hint">No matches.</p> : docs.map((d) => docRow(d, 0)))
        : rows.map((row) => (row.kind === 'folder' ? folderRow(row) : docRow(row.doc as Doc, row.depth)))}

      {!searching && <div className="doc-tree-tail" aria-hidden />}
    </div>
  )
}
