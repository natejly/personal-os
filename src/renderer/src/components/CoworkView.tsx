import { useEffect, useState } from 'react'
import { Archive, PanelLeftOpen, Plus, Users, X } from 'lucide-react'
import type { DeskAutonomy } from '@shared/types'
import { useStore, type Scope } from '../store'
import ScopeSelect from './ScopeSelect'
import DeskRail, { AUTONOMY, railOrder } from './DeskRail'
import DeskDetail from './DeskDetail'
import '../styles/cowork.css'
import AppSwitcher from './AppSwitcher'


/** The brief, and the four knobs that decide how much rope the desk gets. ⌘↵ submits. */
function NewDeskCard({ scope, onDone }: { scope: Scope; onDone: () => void }): JSX.Element {
  const createDesk = useStore((s) => s.createDesk)
  const busy = useStore((s) => s.deskBusy)
  const [brief, setBrief] = useState('')
  const [title, setTitle] = useState('')
  const [autonomy, setAutonomy] = useState<DeskAutonomy>('plan')
  const [start, setStart] = useState(true)
  const [maxTurns, setMaxTurns] = useState('')
  const maxTurnsDefault = useStore((s) => s.settings.deskMaxTurns ?? 12)

  const submit = async (): Promise<void> => {
    if (!brief.trim() || busy) return
    const desk = await createDesk({
      brief: brief.trim(),
      title: title.trim() || undefined,
      project_id: scope === 'all' || scope === 'personal' ? null : scope,
      autonomy,
      // Only what the user typed: an empty box means "the Settings cap", and a desk may only tighten it.
      budget: {
        ...(Number(maxTurns) > 0 ? { maxTurns: Number(maxTurns) } : {})
      },
      start
    })
    // `createDesk` resolves null rather than rejecting, so a refused desk keeps the typed brief.
    if (desk) onDone()
  }

  return (
    <section className="cowork-main">
      <div className="desk-new">
        <header>
          <h3>New desk</h3>
          <span className="spacer" />
          <button className="icon-btn ghost" title="Cancel" onClick={onDone}><X size={14} /></button>
        </header>
        <label className="desk-field">
          <span>Brief</span>
          <textarea
            autoFocus
            rows={6}
            placeholder={'What should it do, and what does done look like?\n\nIt works in its own folder and brings files back for you to review.'}
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void submit() } }}
          />
        </label>
        <label className="desk-field">
          <span>Title</span>
          <input placeholder="Taken from the brief if you leave this blank" value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <div className="desk-field">
          <span>Autonomy</span>
          <div className="desk-autonomy">
            {AUTONOMY.map((a) => (
              <label key={a.value} className={`desk-autonomy-opt ${autonomy === a.value ? 'on' : ''}`}>
                <input type="radio" name="autonomy" checked={autonomy === a.value} onChange={() => setAutonomy(a.value)} />
                <b>{a.label}</b>
                <small>{a.hint}</small>
              </label>
            ))}
          </div>
        </div>
        <div className="desk-field">
          <span>Limits</span>
          <div className="desk-limits">
            <label>Turns <input type="number" min={1} step={1} placeholder={String(maxTurnsDefault)} value={maxTurns} onChange={(e) => setMaxTurns(e.target.value)} /></label>
            <small className="muted">Blank uses your Settings caps. A desk can only be held tighter than those.</small>
          </div>
        </div>
        <label className="chip-check-row">
          <input type="checkbox" checked={start} onChange={(e) => setStart(e.target.checked)} />
          <span>Start it now</span>
        </label>
        <footer>
          <button className="ghost-btn" onClick={onDone}>Cancel</button>
          <button className="primary-btn" disabled={!brief.trim() || busy} onClick={() => void submit()}>
            <Plus size={14} /> {start ? 'Create and start' : 'Create draft'} <kbd>⌘↵</kbd>
          </button>
        </footer>
      </div>
    </section>
  )
}

export default function CoworkView(): JSX.Element {
  const desks = useStore((s) => s.desks)
  const activeDeskId = useStore((s) => s.activeDeskId)
  const libraryScope = useStore((s) => s.libraryScope)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const showArchived = useStore((s) => s.deskShowArchived)
  const { refreshDesks, refreshDeskInbox, openDesk, toggleSidebar, setLibraryScope, setDeskShowArchived } = useStore()
  const [creating, setCreating] = useState(false)

  const scope: Scope = libraryScope
  useEffect(() => { void refreshDesks(); void refreshDeskInbox() }, [refreshDesks, refreshDeskInbox, scope])

  // `[` and `]` step the rail in the order it is drawn. Ignored while a field has focus, where the
  // brackets are just characters.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== '[' && e.key !== ']') return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const el = e.target as HTMLElement | null
      if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) return
      const order = railOrder(desks)
      if (order.length === 0) return
      const at = order.findIndex((d) => d.id === activeDeskId)
      const next = e.key === ']'
        ? order[at < 0 ? 0 : (at + 1) % order.length]
        : order[at < 0 ? order.length - 1 : (at - 1 + order.length) % order.length]
      e.preventDefault()
      void openDesk(next.id)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [desks, activeDeskId, openDesk])

  return (
    <main className="page cowork-page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" title="Show sidebar (⌘B)" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><Users size={16} /> Cowork</h2>
        <div className="no-drag header-right">
          <ScopeSelect value={scope} onChange={(s) => void setLibraryScope(s)} />
          <button
            className={`ghost-btn ${showArchived ? 'active' : ''}`}
            aria-pressed={showArchived}
            title={showArchived ? 'Back to current desks' : 'Show archived desks'}
            onClick={() => void setDeskShowArchived(!showArchived)}
          >
            <Archive size={13} /> {showArchived ? 'Archived' : 'Archive'}
          </button>
          <button className="primary-btn" onClick={() => setCreating(true)}><Plus size={14} /> New desk</button>
        </div>
        <AppSwitcher />
      </header>

      <div className="cowork-body">
        <aside className="cowork-side">
          <DeskRail desks={desks} activeId={creating ? null : activeDeskId} onOpen={(id) => { setCreating(false); void openDesk(id) }} />
        </aside>

        {creating ? (
          <NewDeskCard scope={scope} onDone={() => setCreating(false)} />
        ) : activeDeskId ? (
          <DeskDetail />
        ) : (
          <section className="cowork-empty">
            <Users size={30} />
            <h2>No desk open</h2>
            <p className="muted">Start one when you have a task to hand off.</p>
            <button className="primary-btn" onClick={() => setCreating(true)}><Plus size={14} /> New desk</button>
          </section>
        )}
      </div>
    </main>
  )
}
