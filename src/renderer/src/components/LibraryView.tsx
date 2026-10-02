import { useEffect, useState } from 'react'
import { BookOpen, FileText, KanbanSquare, LayoutDashboard, Library, Package, PanelLeftOpen, Plug, Sparkles, Workflow } from 'lucide-react'
import { useStore, type LibraryTab } from '../store'
import { api } from '../lib/api'
import { rowButton } from '../lib/rowButton'
import type { Artifact, Board, Dashboard } from '@shared/types'
import SkillsPanel from './SkillsPanel'
import McpSettings from './McpSettings'
import WorkflowsPanel from './WorkflowsPanel'
import AppSwitcher from './AppSwitcher'
import ArtifactsView from './ArtifactsView'
import ArtifactViewer from './ArtifactViewer'

const TABS: { key: LibraryTab; label: string; icon: JSX.Element; blurb: string }[] = [
  { key: 'skills', label: 'Skills', icon: <Sparkles size={14} />, blurb: 'Ways of doing a task the assistant may follow again' },
  { key: 'workflows', label: 'Workflows', icon: <Workflow size={14} />, blurb: 'Repeatable multi-step jobs you approve once, by hash' },
  { key: 'connectors', label: 'Connectors', icon: <Plug size={14} />, blurb: 'MCP servers whose tools the assistant can call' },
  { key: 'artifacts', label: 'Artifacts', icon: <Package size={14} />, blurb: 'Interactive pages the assistant built, with version history' },
  { key: 'made', label: 'Made', icon: <BookOpen size={14} />, blurb: 'Everything built in this app, in one place' }
]

/** One row of the Made tab: anything with a name, a kind and a view that can open it. */
interface Made {
  id: string
  kind: 'doc' | 'dashboard' | 'board' | 'artifact'
  name: string
  meta: string
  at: number
}

const KIND_ICON: Record<Made['kind'], JSX.Element> = {
  doc: <FileText size={14} />, artifact: <Package size={14} />, dashboard: <LayoutDashboard size={14} />, board: <KanbanSquare size={14} />
}

function MadePanel(): JSX.Element {
  const docs = useStore((s) => s.docs)
  const { setView, openDoc } = useStore()
  const [extra, setExtra] = useState<Made[]>([])
  const [kind, setKind] = useState<'all' | Made['kind']>('all')
  const [q, setQ] = useState('')
  const [viewing, setViewing] = useState<string | null>(null)

  // Boards and dashboards live in their own views, so the Library fetches them rather than holding them.
  useEffect(() => {
    let live = true
    void Promise.all([api.dashboards.list().catch(() => [] as Dashboard[]), api.boards.list().catch(() => [] as Board[]), api.artifacts.list().catch(() => [] as Artifact[])])
      .then(([dashboards, boards, arts]) => {
        if (!live) return
        setExtra([
          ...arts.map((a) => ({ id: a.id, kind: 'artifact' as const, name: a.title || 'Untitled', meta: `artifact · v${a.version}`, at: a.updated_at })),
          ...dashboards.map((d) => ({ id: d.id, kind: 'dashboard' as const, name: d.name, meta: d.description || 'dashboard', at: d.created_at })),
          ...boards.map((b) => ({ id: b.id, kind: 'board' as const, name: b.name, meta: `${b.card_count ?? b.cards?.length ?? 0} cards`, at: b.created_at }))
        ])
      })
    return () => { live = false }
  }, [])

  const rows: Made[] = [
    ...docs.map((d) => ({ id: d.id, kind: 'doc' as const, name: d.title || 'Untitled', meta: d.folder || 'doc', at: d.updated_at ?? 0 })),
    ...extra
  ]
    .filter((r) => (kind === 'all' || r.kind === kind) && (!q.trim() || r.name.toLowerCase().includes(q.trim().toLowerCase())))
    .sort((a, b) => b.at - a.at)
  const filtered = kind !== 'all' || q.trim() !== ''

  const open = (r: Made): void => {
    if (r.kind === 'doc') void openDoc(r.id)
    else if (r.kind === 'artifact') setViewing(r.id)
    else setView(r.kind === 'board' ? 'boards' : 'dashboards')
  }

  return (
    <div className="library-panel">
      <div className="add-row">
        <div className="seg">
          {(['all', 'doc', 'artifact', 'dashboard', 'board'] as const).map((k) => (
            <button key={k} className={kind === k ? 'on' : ''} onClick={() => setKind(k)}>{k === 'all' ? 'everything' : `${k}s`}</button>
          ))}
        </div>
        <input className="search" value={q} placeholder="Search by name" onChange={(e) => setQ(e.target.value)} />
      </div>
      {rows.length === 0 ? (
        <div className="empty-state">
          <BookOpen size={28} />
          <h2>{filtered ? 'Nothing matches' : 'Nothing made yet'}</h2>
          <p>{filtered ? 'No doc, artifact, dashboard or board fits that filter.' : 'Docs, artifacts, dashboards and boards that you or the assistant create show up here.'}</p>
          {filtered && <button className="primary-btn" onClick={() => { setKind('all'); setQ('') }}>Show everything</button>}
        </div>
      ) : (
        <div className="made-grid">
          {rows.map((r) => (
            <div key={`${r.kind}:${r.id}`} className="made-card" {...rowButton(() => open(r))}>
              <div className="made-head">{KIND_ICON[r.kind]}<span className="made-name" title={r.name}>{r.name}</span></div>
              <div className="made-meta muted small">{r.meta}{r.at ? ` · ${new Date(r.at * 1000).toLocaleDateString()}` : ''}</div>
            </div>
          ))}
        </div>
      )}
      {viewing && <ArtifactViewer id={viewing} onClose={() => setViewing(null)} />}
    </div>
  )
}

export default function LibraryView(): JSX.Element {
  const tab = useStore((s) => s.libraryTab)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const candidates = useStore((s) => s.skills.filter((x) => x.status === 'candidate').length)
  const { setLibraryTab, toggleSidebar, refreshLibrary } = useStore()

  // Cheap enough to re-run on every entry, and it is the only thing that notices a connector that
  // fell over while the view was closed.
  useEffect(() => { void refreshLibrary().catch(() => undefined) }, [refreshLibrary])

  return (
    <main className="page library-page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" aria-label="Show sidebar" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><Library size={16} /> Library</h2>
        <AppSwitcher />
      </header>
      {/* The shelves are sections of this page, so they are tabs under the header, not a switch in it. */}
      <div className="library-tabs tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? 'active' : ''} title={t.blurb} onClick={() => setLibraryTab(t.key)}>
            {t.icon} {t.label}
            {t.key === 'skills' && candidates > 0 && <span className="count" title={`${candidates} waiting for you`}>{candidates}</span>}
          </button>
        ))}
      </div>
      <div className="page-body">
        {tab === 'skills' && <SkillsPanel />}
        {tab === 'workflows' && <WorkflowsPanel />}
        {tab === 'connectors' && <div className="library-panel"><McpSettings /></div>}
        {tab === 'artifacts' && <ArtifactsView />}
        {tab === 'made' && <MadePanel />}
      </div>
    </main>
  )
}
