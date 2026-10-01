import { useEffect, useState } from 'react'
import { Search, PanelLeftOpen, Brain, Columns2, List, Share2, PenLine } from 'lucide-react'
import { useStore, type MemoryMode, type Scope } from '../store'
import MemoryView from './MemoryView'
import GraphView from './GraphView'
import StyleView from './StyleView'
import ScopeSelect from './ScopeSelect'
import SendToSpace from './SendToSpace'

const MODES: { key: MemoryMode; label: string; icon: JSX.Element; title: string }[] = [
  { key: 'split', label: 'Split', icon: <Columns2 size={13} />, title: 'Memories and graph side by side' },
  { key: 'list', label: 'List', icon: <List size={13} />, title: 'Memories only' },
  { key: 'graph', label: 'Graph', icon: <Share2 size={13} />, title: 'Knowledge graph only' },
  { key: 'style', label: 'Voice', icon: <PenLine size={13} />, title: 'How you write, and the samples it was learned from' }
]

/**
 * Memory: one panel holding both halves of what the app remembers — the memory
 * list and the knowledge graph — over a shared scope filter and search box.
 * `projectId` scopes it to a project (used inside ProjectView); without it the
 * panel follows the library scope and renders its own page header.
 */
export default function MemoryPanel({ projectId, embedded = false }: { projectId?: string; embedded?: boolean }): JSX.Element {
  const libraryScope = useStore((s) => s.libraryScope)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const memories = useStore((s) => s.memories)
  const graph = useStore((s) => s.graph)
  const mode = useStore((s) => s.memoryMode)
  const { toggleSidebar, setLibraryScope, loadScope, setMemoryMode } = useStore()
  const style = useStore((s) => s.style)
  const scope: Scope = projectId ?? libraryScope
  const [q, setQ] = useState('')
  const samples = style?.stats.samples ?? 0
  const styleCount = `${style?.profile ? 'voice learned' : 'no voice yet'} · ${samples} sample${samples === 1 ? '' : 's'}`

  useEffect(() => { void loadScope(scope) }, [scope, loadScope])

  // 'style' is a page of its own: a voice profile has nothing to sit side by side with.
  const showStyle = mode === 'style'
  const showList = !showStyle && mode !== 'graph'
  const showGraph = !showStyle && mode !== 'list'

  const modeToggle = (
    <div className="seg" role="group" aria-label="Memory layout">
      {MODES.map((m) => (
        <button key={m.key} className={mode === m.key ? 'active' : ''} title={m.title} onClick={() => setMemoryMode(m.key)}>
          {m.icon}<span>{m.label}</span>
        </button>
      ))}
    </div>
  )
  const search = showStyle ? null : (
    <label className="search"><Search size={14} /><input placeholder={showGraph && !showList ? 'Find entity' : 'Search memory'} value={q} onChange={(e) => setQ(e.target.value)} /></label>
  )

  const body = (
    <div className={`memory-body mode-${mode}`}>
      {showStyle && <StyleView projectId={projectId} embedded={embedded} />}
      {showGraph && <GraphView projectId={projectId} query={q} />}
      {showList && (
        <div className="mem-pane">
          <MemoryView projectId={projectId} query={q} />
        </div>
      )}
    </div>
  )

  if (embedded) {
    return (
      <div className="memory-panel embedded">
        <div className="memory-toolbar">
          <span className="muted small">{showStyle ? styleCount : `${memories.length} memor${memories.length === 1 ? 'y' : 'ies'} · ${graph.nodes.length} entit${graph.nodes.length === 1 ? 'y' : 'ies'}, ${graph.edges.length} relation${graph.edges.length === 1 ? '' : 's'}`}</span>
          <div className="toolbar-right">{search}{modeToggle}</div>
        </div>
        {body}
      </div>
    )
  }

  return (
    <main className="page memory-panel">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" aria-label="Show sidebar" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><Brain size={16} /> Memory <span className="muted">· {showStyle ? styleCount : `${memories.length} memor${memories.length === 1 ? 'y' : 'ies'}, ${graph.nodes.length} entit${graph.nodes.length === 1 ? 'y' : 'ies'}`}</span></h2>
        <div className="no-drag header-right">
          <SendToSpace items={[{ kind: mode === 'graph' ? 'graph' : 'memory' }]} />
          <ScopeSelect value={libraryScope} onChange={(s) => void setLibraryScope(s)} />
          {search}
          {modeToggle}
        </div>
      </header>
      {body}
    </main>
  )
}
