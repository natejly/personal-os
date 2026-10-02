import { useCallback, useEffect, useMemo, useState } from 'react'
import { Calendar, KanbanSquare, Plus, X } from 'lucide-react'
import type { Board, BoardCard, BoardColumn } from '@shared/types'
import { useStore } from '../../store'
import { api } from '../../lib/api'
import { dragProps, useDropTarget, type DropHandler } from '../dnd'
import type { WidgetDef, WidgetProps } from '../registry'

const POLL_MS = 30_000
const ACCEPTS = ['todo', 'board-card'] as const

const due = (d: string): { label: string; overdue: boolean } => ({
  label: new Date(d + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
  overdue: new Date(d + 'T00:00:00') < new Date(new Date().toDateString())
})

interface ColumnProps {
  board: Board
  col: BoardColumn
  cards: BoardCard[]
  onChange: () => void
  onError: (m: string) => void
}

function Column({ board, col, cards, onChange, onError }: ColumnProps): JSX.Element {
  const [title, setTitle] = useState('')
  const fail = (e: unknown): void => onError((e as Error).message)
  const add = (t: string): void => { void api.boards.addCard(board.id, { title: t, column_id: col.id }).then(onChange).catch(fail) }

  const onDrop: DropHandler = (p, e) => {
    if (!p) return
    if (p.kind === 'todo' || !board.cards.some((c) => c.id === p.id)) return add(p.label)
    const els = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('.kcard-wrap'))
    const before = els.find((el) => e.clientY < el.getBoundingClientRect().top + el.offsetHeight / 2 && el.dataset.id !== p.id)
    void api.boards.moveCard(p.id, col.id, before?.dataset.id ?? null).then(onChange).catch(fail)
  }
  const drop = useDropTarget([...ACCEPTS], onDrop)

  return (
    <div className={`kcol ${drop.over ? 'over drop-over' : ''}`} {...drop.handlers}>
      <header>
        <span className="kcol-name">{col.name}</span>
        <span className={`count${col.wip_limit && cards.length > col.wip_limit ? ' over-limit' : ''}`}>{cards.length}{col.wip_limit ? `/${col.wip_limit}` : ''}</span>
      </header>
      <div className="kcol-cards">
        {cards.map((c) => (
          <div key={c.id} data-id={c.id} className="kcard-wrap">
            <div className={`kcard p${c.priority}`} {...dragProps({ kind: 'board-card', id: c.id, label: c.title, projectId: board.project_id })}>
              <div className="kcard-title">{c.title}</div>
              <div className="kcard-meta">
                {c.due && <span className={`tag ${due(c.due).overdue ? 'overdue' : ''}`}><Calendar size={10} />{due(c.due).label}</span>}
                {c.labels.map((l) => <span key={l} className="tag">{l}</span>)}
                {c.priority === 1 && <span className="tag p1">P1</span>}
                <span style={{ flex: 1 }} />
                <button className="icon-btn ghost sm" title="Delete card" onClick={() => void api.boards.deleteCard(c.id).then(onChange).catch(fail)}><X size={11} /></button>
              </div>
            </div>
          </div>
        ))}
        <div className="kcol-addbtn">
          <Plus size={13} />
          <input className="widget-input" placeholder="Add card" value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && title.trim()) { add(title.trim()); setTitle('') } if (e.key === 'Escape') setTitle('') }} />
        </div>
      </div>
    </div>
  )
}

const BoardWidget = ({ window: win, live, onTitle }: WidgetProps): JSX.Element => {
  const toast = useStore((s) => s.toast)
  const [board, setBoard] = useState<Board | null>(null)
  const [error, setError] = useState<string | null>(null)
  const refId = win.ref_id
  const colId = typeof win.config.column_id === 'string' ? win.config.column_id : null

  const load = useCallback(async (): Promise<void> => {
    if (!refId) return
    try {
      setBoard(await api.boards.get(refId))
      setError(null)
    } catch (e) {
      setBoard(null)
      setError((e as Error).message)
    }
  }, [refId])

  // `live` gates the poller: off-screen, minimized or zoomed-out boards neither fetch nor tick.
  useEffect(() => {
    if (!live || !refId) return
    void load()
    const t = setInterval(() => void load(), POLL_MS)
    return () => clearInterval(t)
  }, [live, refId, load])

  useEffect(() => {
    if (board && !win.title) onTitle(board.name)
  }, [board, win.title, onTitle])

  const byCol = useMemo(() => {
    const m: Record<string, BoardCard[]> = {}
    for (const c of board?.cards ?? []) (m[c.column_id] ??= []).push(c)
    return m
  }, [board])

  const onError = useCallback((m: string) => toast(m, 'error'), [toast])
  const reload = useCallback(() => void load(), [load])
  const onDrop: DropHandler = (p) => {
    const first = board?.columns[0]
    if (!p || !board || !first) return
    if (p.kind === 'todo' || !board.cards.some((c) => c.id === p.id)) {
      void api.boards.addCard(board.id, { title: p.label, column_id: colId ?? first.id }).then(reload).catch((e) => onError((e as Error).message))
    }
  }
  const drop = useDropTarget([...ACCEPTS], onDrop)

  if (!refId) return <div className="widget-empty"><span>This window has no board.</span></div>

  // Off-screen or zoomed out: every column, every card and every column drop target unmounts (§6).
  if (!live) {
    return (
      <div className="proxy-card">
        <KanbanSquare size={18} />
        <strong>{board?.name || win.title || 'Board'}</strong>
        <span>{board ? `${board.cards.length} cards · paused` : 'Paused while off-screen'}</span>
      </div>
    )
  }

  if (error && !board) return <div className="widget-error"><span>{error}</span><button className="widget-chip" onClick={reload}>Retry</button></div>
  if (!board) return <div className="widget-empty"><span>Loading…</span></div>

  const cols = colId ? board.columns.filter((c) => c.id === colId) : board.columns

  return (
    <div className={`widget ${drop.over ? 'drop-over' : ''}`} {...drop.handlers}>
      <div className="widget-bar">
        <KanbanSquare size={11} />
        <span className="widget-title grow">{board.name}</span>
        <span className="widget-meta">{board.cards.length} cards</span>
      </div>
      {cols.length === 0 ? (
        <div className="widget-empty"><span>{colId ? 'That column is gone.' : 'No columns yet.'}</span></div>
      ) : (
        <div className="kanban">
          {cols.map((c) => <Column key={c.id} board={board} col={c} cards={byCol[c.id] ?? []} onChange={reload} onError={onError} />)}
        </div>
      )}
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'board',
  label: 'Board',
  icon: <KanbanSquare size={18} />,
  defaultSize: { w: 760, h: 560 },
  minSize: { w: 420, h: 320 },
  chrome: 'full',
  needsRef: true,
  accepts: [...ACCEPTS],
  Component: BoardWidget
}

export default BoardWidget
