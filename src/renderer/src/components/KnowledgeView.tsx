import { useEffect, useState } from 'react'
import { BookOpen, Brain, FileText, PanelLeftOpen, Search, Share2 } from 'lucide-react'
import { useStore, type MemoryMode } from '../store'
import DocumentsView from './DocumentsView'
import MemoryView from './MemoryView'
import GraphView from './GraphView'
import ScopeSelect from './ScopeSelect'

type Section = 'library' | 'memory' | 'graph'

const SECTIONS: { key: Section; label: string; icon: JSX.Element; title: string }[] = [
  { key: 'library', label: 'Library', icon: <FileText size={13} />, title: 'Uploaded documents' },
  { key: 'memory', label: 'Memory', icon: <Brain size={13} />, title: 'What the app remembers' },
  { key: 'graph', label: 'Graph', icon: <Share2 size={13} />, title: 'Knowledge graph' }
]

/** `openMemory(mode)` is still how the rest of the app asks for a half of the old Memory panel. */
const sectionFor = (m: MemoryMode): Section => (m === 'graph' ? 'graph' : m === 'list' ? 'memory' : 'library')

/**
 * Knowledge: the library of uploaded documents, the memory list and the knowledge
 * graph behind one page header — a shared scope filter, one search box and a
 * section switch. Every section stays mounted: the graph keeps its simulation's
 * node positions (paused, per GraphView's contract) and the library keeps the
 * `doc-upload-input` element in the DOM for the ⌘U menu action.
 */
export default function KnowledgeView(): JSX.Element {
  const libraryScope = useStore((s) => s.libraryScope)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const memories = useStore((s) => s.memories)
  const graph = useStore((s) => s.graph)
  const documents = useStore((s) => s.documents)
  const memoryMode = useStore((s) => s.memoryMode)
  const toggleSidebar = useStore((s) => s.toggleSidebar)
  const setLibraryScope = useStore((s) => s.setLibraryScope)
  const loadScope = useStore((s) => s.loadScope)
  const [section, setSection] = useState<Section>(() => sectionFor(useStore.getState().memoryMode))
  const [q, setQ] = useState('')

  useEffect(() => { void loadScope(libraryScope) }, [libraryScope, loadScope])
  // ContextDrawer's "edit" links call openMemory('list' | 'graph') while this page may already be up.
  useEffect(() => { setSection(sectionFor(memoryMode)) }, [memoryMode])

  return (
    <main className="page knowledge-panel">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><BookOpen size={16} /> Knowledge <span className="muted">· {documents.length} documents, {memories.length} memories, {graph.nodes.length} entities</span></h2>
        <div className="no-drag header-right">
          <ScopeSelect value={libraryScope} onChange={(s) => void setLibraryScope(s)} />
          <label className="search"><Search size={14} /><input placeholder={section === 'graph' ? 'Find entity' : 'Search memory'} value={q} onChange={(e) => setQ(e.target.value)} /></label>
          <div className="seg" role="group" aria-label="Knowledge section">
            {SECTIONS.map((s) => (
              <button key={s.key} className={section === s.key ? 'active' : ''} title={s.title} onClick={() => setSection(s.key)}>
                {s.icon}<span>{s.label}</span>
              </button>
            ))}
          </div>
        </div>
      </header>
      <div className="knowledge-body">
        <div className={`knowledge-pane ${section === 'library' ? 'active' : ''}`}>
          <DocumentsView embedded />
        </div>
        <div className={`knowledge-pane ${section === 'memory' ? 'active' : ''}`}>
          <MemoryView query={q} />
        </div>
        <div className={`knowledge-pane ${section === 'graph' ? 'active' : ''}`}>
          <GraphView query={q} paused={section !== 'graph'} />
        </div>
      </div>
    </main>
  )
}
