import { useEffect, useMemo, useRef, useState } from 'react'
import { Plus, Trash2, PanelLeftOpen, KanbanSquare, Calendar, X, ChevronDown } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import type { Board, BoardCard, BoardColumn } from '@shared/types'
import ProjectChip from './ProjectChip'
import SendToSpace from './SendToSpace'
import { clearHandoff, peekHandoff } from '../lib/handoff'
import { lines, usePageContext } from '../lib/pageContext'
import AppSwitcher from './AppSwitcher'

const PRIO = ['', 'P1', 'P2', 'P3']

function Card({ card, onOpen, onDragStart }: { card: BoardCard; onOpen: () => void; onDragStart: (e: React.DragEvent) => void }): JSX.Element {
  const overdue = card.due && new Date(card.due + 'T00:00:00') < new Date(new Date().toDateString())
  return (
    <div className={`kcard p${card.priority}`} draggable onDragStart={onDragStart} onClick={onOpen}>
      <div className="kcard-title">{card.title}</div>
      {(card.due || card.labels.length > 0) && (
        <div className="kcard-meta">
          {card.due && <span className={`tag ${overdue ? 'overdue' : ''}`}><Calendar size={10} />{new Date(card.due + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span>}
          {card.labels.map((l) => <span key={l} className="tag">{l}</span>)}
          {card.priority === 1 && <span className="tag p1">P1</span>}
        </div>
      )}
    </div>
  )
}

function CardModal({ card, board, onClose, onChange }: { card: BoardCard; board: Board; onClose: () => void; onChange: () => void }): JSX.Element {
  const [title, setTitle] = useState(card.title)
  const [desc, setDesc] = useState(card.description)
  const [due, setDue] = useState(card.due ?? '')
  const [priority, setPriority] = useState(card.priority)
  const [labels, setLabels] = useState(card.labels.join(', '))
  const save = async (): Promise<void> => {
    await api.boards.updateCard(card.id, { title: title.trim() || card.title, description: desc, due: due || undefined, clear_due: !due, priority, labels: labels.split(',').map((s) => s.trim()).filter(Boolean) })
    onChange(); onClose()
  }
  const col = board.columns.find((c) => c.id === card.column_id)
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <header><h2>Card</h2><button className="icon-btn" aria-label="Close card" onClick={onClose}><X size={16} /></button></header>
        <section>
          <label><span>Title</span><input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} /></label>
          <label><span>Description</span><textarea rows={5} value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Details, links, acceptance criteria…" /></label>
          <div className="row3">
            <label><span>Due</span><input type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label>
            <label><span>Priority</span><select value={priority} onChange={(e) => setPriority(Number(e.target.value))}><option value={1}>P1 · high</option><option value={2}>P2 · normal</option><option value={3}>P3 · low</option></select></label>
            <label><span>Column</span><select value={card.column_id} onChange={(e) => void api.boards.moveCard(card.id, e.target.value).then(onChange)}>{board.columns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
          </div>
          <label><span>Labels <small className="muted">(comma separated)</small></span><input value={labels} onChange={(e) => setLabels(e.target.value)} placeholder="design, blocked, v0.1" /></label>
          <p className="muted small">In {col?.name} · created {new Date(card.created_at * 1000).toLocaleDateString()}</p>
        </section>
        <footer>
          <button className="ghost-btn danger" onClick={() => void api.boards.deleteCard(card.id).then(() => { onChange(); onClose() })}><Trash2 size={14} /> Delete</button>
          <span style={{ flex: 1 }} />
          <button className="ghost-btn" onClick={onClose}>Cancel</button>
          <button className="primary-btn" onClick={() => void save()}>Save</button>
        </footer>
      </div>
    </div>
  )
}

function Column({ col, cards, board, onChange, onOpen }: { col: BoardColumn; cards: BoardCard[]; board: Board; onChange: () => void; onOpen: (c: BoardCard) => void }): JSX.Element {
  const [adding, setAdding] = useState(false)
  const [title, setTitle] = useState('')
  const [over, setOver] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const add = async (): Promise<void> => {
    if (!title.trim()) return
    await api.boards.addCard(board.id, { title: title.trim(), column_id: col.id })
    setTitle(''); onChange()
  }
  const onDrop = async (e: React.DragEvent): Promise<void> => {
    e.preventDefault(); setOver(false)
    const cardId = e.dataTransfer.getData('text/card')
    if (!cardId) return
    // find the card we're dropping before (by y position)
    const els = Array.from((e.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('.kcard'))
    const before = els.find((el) => e.clientY < el.getBoundingClientRect().top + el.offsetHeight / 2 && el.dataset.id !== cardId)
    await api.boards.moveCard(cardId, col.id, before?.dataset.id ?? null)
    onChange()
  }
  return (
    <div className={`kcol ${over ? 'over' : ''}`} onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)} onDrop={(e) => void onDrop(e)}>
      <header>
        {renaming ? (
          <input autoFocus defaultValue={col.name} onBlur={(e) => { void api.boards.updateColumn(col.id, { name: e.target.value }).then(onChange); setRenaming(false) }} onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()} />
        ) : (
          <span className="kcol-name" onClick={() => setRenaming(true)}>{col.name}</span>
        )}
        <span className="count">{cards.length}{col.wip_limit ? `/${col.wip_limit}` : ''}</span>
        <button className="icon-btn ghost sm" title="Add card" aria-label={`Add card to ${col.name}`} onClick={() => setAdding(true)}><Plus size={14} /></button>
        <button className="icon-btn ghost sm danger" title="Delete column" aria-label={`Delete column ${col.name}`} onClick={() => { if (cards.length === 0 || confirm(`Delete "${col.name}" and its ${cards.length} cards?`)) void api.boards.deleteColumn(col.id).then(onChange) }}><Trash2 size={13} /></button>
      </header>
      <div className="kcol-cards">
        {cards.map((c) => (
          <div key={c.id} data-id={c.id} className="kcard-wrap">
            <Card card={c} onOpen={() => onOpen(c)} onDragStart={(e) => { e.dataTransfer.setData('text/card', c.id); e.dataTransfer.effectAllowed = 'move' }} />
          </div>
        ))}
        {adding ? (
          <div className="kcard-add">
            <textarea autoFocus rows={2} value={title} placeholder="Card title" onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void add() } if (e.key === 'Escape') { setAdding(false); setTitle('') } }} />
            <div className="row"><button className="primary-btn" onClick={() => void add()}>Add</button><button className="ghost-btn" onClick={() => { setAdding(false); setTitle('') }}>Cancel</button></div>
          </div>
        ) : (
          <button className="kcol-addbtn" onClick={() => setAdding(true)}><Plus size={13} /> Add card</button>
        )}
      </div>
    </div>
  )
}

export default function BoardsView(): JSX.Element {
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const projects = useStore((s) => s.projects)
  const { toggleSidebar, toast } = useStore()
  const [boards, setBoards] = useState<Board[]>([])
  // A canvas board window's Expand hands its board over; a stale id falls back to the first board.
  const [activeId, setActiveId] = useState<string | null>(() => peekHandoff('board'))
  const [board, setBoard] = useState<Board | null>(null)
  const [open, setOpen] = useState<BoardCard | null>(null)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const colName = useRef<HTMLInputElement>(null)

  const loadList = async (): Promise<void> => {
    const list = await api.boards.list()
    setBoards(list)
    if (list.length && (!activeId || !list.some((b) => b.id === activeId))) setActiveId(list[0].id)
  }
  const loadBoard = async (): Promise<void> => { if (activeId) setBoard(await api.boards.get(activeId)) }
  useEffect(() => { clearHandoff('board') }, [])
  useEffect(() => { void loadList() }, [])
  useEffect(() => { setConfirmDelete(false); void loadBoard() }, [activeId])

  const create = async (): Promise<void> => {
    if (!newName.trim()) return
    const b = await api.boards.create({ name: newName.trim() })
    setNewName(''); setCreating(false)
    await loadList(); setActiveId(b.id)
  }
  const byCol = useMemo(() => {
    const m: Record<string, BoardCard[]> = {}
    for (const c of board?.cards ?? []) (m[c.column_id] ??= []).push(c)
    return m
  }, [board])

  usePageContext(() => (board
    ? {
        view: 'boards',
        label: `Board “${board.name}”`,
        detail: [
          `Board \`${board.id}\` is open.`,
          ...board.columns.map((col) => {
            const cards = byCol[col.id] ?? []
            return `### ${col.name} (${cards.length})\n${cards.length ? lines(cards, (c) => `${c.title} (\`${c.id}\`)${c.due ? `, due ${c.due}` : ''}${c.labels.length ? `, labels: ${c.labels.join(', ')}` : ''}`, 20) : '- (empty)'}`
          }),
          open ? `The user has this card open: “${open.title}” (\`${open.id}\`)\n${open.description}` : ''
        ].filter(Boolean).join('\n\n'),
        refs: [{ kind: 'board', id: board.id, name: board.name }, ...(open ? [{ kind: 'card', id: open.id, name: open.title }] : [])],
        hints: ['What is stuck in this board?', 'Add cards for the next steps', 'Summarise progress for a standup']
      }
    : {
        view: 'boards',
        label: 'Boards',
        detail: boards.length ? `Boards:\n${lines(boards, (b) => `${b.name} (\`${b.id}\`, ${b.card_count ?? 0} cards)`)}` : 'No boards yet.',
        refs: boards.slice(0, 40).map((b) => ({ kind: 'board', id: b.id, name: b.name })),
        hints: ['Make a board for this project']
      }), [board, boards, byCol, open])

  return (
    <main className="page board-page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" aria-label="Show sidebar" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><KanbanSquare size={16} /> Boards</h2>
        <div className="no-drag header-right">
          <SendToSpace items={[{ kind: 'board', refId: activeId }]} disabled={!activeId} />
          {/* Delete sits at the far end from "New board", and takes two clicks (same pattern as ProjectModal). */}
          {board && (confirmDelete
            ? <button className="ghost-btn danger" onClick={() => void api.boards.delete(board.id).then(() => { setConfirmDelete(false); setActiveId(null); setBoard(null); void loadList() })}><Trash2 size={14} /> Really delete this board and its cards</button>
            : <button className="icon-btn danger" title="Delete board" aria-label={`Delete board ${board.name}`} onClick={() => setConfirmDelete(true)}><Trash2 size={15} /></button>
          )}
          {boards.length > 0 && (
            <label className="model-picker">
              <select aria-label="Active board" value={activeId ?? ''} onChange={(e) => setActiveId(e.target.value)}>{boards.map((b) => <option key={b.id} value={b.id}>{b.name} ({b.card_count})</option>)}</select>
              <ChevronDown size={14} />
            </label>
          )}
          {board && (
            <label className="model-picker" title="Project">
              <select aria-label="Board project" value={board.project_id ?? ''} onChange={(e) => void api.boards.update(board.id, { project_id: e.target.value || null }).then(loadBoard)}>
                <option value="">No project</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <ChevronDown size={14} />
            </label>
          )}
          {creating ? (
            <div className="add-inline"><input autoFocus aria-label="Board name" placeholder="Board name" value={newName} onChange={(e) => setNewName(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') void create(); if (e.key === 'Escape') setCreating(false) }} /><button className="primary-btn" onClick={() => void create()}>Create</button></div>
          ) : (
            <button className="primary-btn" onClick={() => setCreating(true)}><Plus size={14} /> New board</button>
          )}
        </div>
        <AppSwitcher />
      </header>
      {!board ? (
        <div className="page-body">
          <div className="empty-hint big">
            <p>No boards yet. Create one, or ask the assistant: “make a board for my apartment move”.</p>
            <button className="primary-btn" onClick={() => setCreating(true)}><Plus size={14} /> New board</button>
          </div>
        </div>
      ) : (
        <div className="kanban">
          {board.columns.map((col) => <Column key={col.id} col={col} cards={byCol[col.id] ?? []} board={board} onChange={() => void loadBoard()} onOpen={setOpen} />)}
          <div className="kcol new">
            <input ref={colName} aria-label="Add column" placeholder="+ Add column" onKeyDown={(e) => { if (e.key === 'Enter' && colName.current?.value.trim()) { void api.boards.addColumn(board.id, colName.current.value.trim()).then(() => { colName.current!.value = ''; void loadBoard() }).catch((err) => toast(err.message, 'error')) } }} />
          </div>
        </div>
      )}
      {board?.project_id && <div className="board-foot"><ProjectChip projectId={board.project_id} /></div>}
      {open && board && <CardModal card={open} board={board} onClose={() => setOpen(null)} onChange={() => void loadBoard()} />}
    </main>
  )
}
