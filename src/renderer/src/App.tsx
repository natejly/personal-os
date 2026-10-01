import { useEffect, useRef } from 'react'
import { useStore } from './store'
import { watchSelection } from './lib/pageContext'
import Sidebar from './components/Sidebar'
import ChatView from './components/ChatView'
import MemoryPanel from './components/MemoryPanel'
import DocumentsView from './components/DocumentsView'
import DocsView from './components/DocsView'
import ActivityView from './components/ActivityView'
import ProjectView from './components/ProjectView'
import HomeView from './components/HomeView'
import BoardsView from './components/BoardsView'
import CalendarView from './components/CalendarView'
import MailView from './components/MailView'
import DashboardsView from './components/DashboardsView'
import PendingSends from './components/PendingSends'
import PageAgentPanel from './components/PageAgentPanel'
import SettingsModal from './components/SettingsModal'
import ProjectModal from './components/ProjectModal'
import { moduleForView } from './shell/registry'
import Canvas from './canvas/Canvas'
import { useCanvas } from './canvas/store'
import { AlertTriangle } from 'lucide-react'

function Toasts(): JSX.Element {
  const toasts = useStore((s) => s.toasts)
  return (
    <div className="toasts">
      {/* Global, not per-view: a send the assistant queued has to be undoable from wherever you are. */}
      <PendingSends />
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>{t.text}</div>
      ))}
    </div>
  )
}

export default function App(): JSX.Element {
  // One selector per field: destructuring the store subscribes the root of the tree to every set(),
  // and a stream calls patchSession once per token.
  const ready = useStore((s) => s.ready)
  const backendError = useStore((s) => s.backendError)
  const init = useStore((s) => s.init)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const settingsOpen = useStore((s) => s.settingsOpen)
  const projectModal = useStore((s) => s.projectModal)
  const view = useStore((s) => s.view)
  const ModView = moduleForView(view)?.view?.Component
  const theme = useStore((s) => s.settings.theme)
  const inCanvas = useStore((s) => s.view === 'canvas')
  const pageAgentOpen = useStore((s) => s.pageAgentOpen)

  useEffect(() => {
    void init()
  }, [init])
  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])
  // What the user has highlighted rides along with the next ⌘I question, whatever view they are in.
  useEffect(() => watchSelection(), [])
  // Spaces are listed in the sidebar, so the canvas store loads with the app, not on first entry.
  // `load()` is also what registers the canvas store's menu, bus and pop-out listeners; each of their
  // canvas-only actions is guarded on the canvas view.
  useEffect(() => { if (ready && !backendError) void useCanvas.getState().load().catch(() => undefined) }, [ready, backendError])
  // Every widget filters client-side, so entering the canvas view loads each shared dataset once at
  // the widest scope, and re-syncs the spaces.
  const entered = useRef(false)
  useEffect(() => {
    if (!inCanvas) {
      // Leaving the canvas view unmounts the plane, so the 400 ms layout debounce would never fire.
      if (entered.current) void useCanvas.getState().flushLayout()
      // Cleared so re-entering re-syncs: another window or surface can have moved things since.
      entered.current = false
      return
    }
    if (entered.current) return
    entered.current = true
    const s = useStore.getState()
    void useCanvas.getState().load()
    void s.loadScope('all')
    void s.refreshTodos('all', true)
  }, [inCanvas])

  if (!ready) return <div className="app loading" />
  if (backendError) {
    return (
      <div className="app loading">
        <div className="backend-error drag">
          <AlertTriangle size={28} />
          <h2>Backend not running</h2>
          <p>Grain could not start its Python backend.</p>
          <pre>{backendError}</pre>
          <p className="muted">
            Set it up once with <code>cd backend && uv venv && uv pip install -e .</code>, then relaunch.
            Or run it yourself and set <code>PERSONAL_OS_BACKEND_URL</code>.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className={`app ${sidebarOpen ? '' : 'sidebar-collapsed'} ${pageAgentOpen ? 'page-agent-open' : ''}`}>
      <Sidebar />
      {inCanvas ? (
        <Canvas />
      ) : (
        <>
          {view === 'home' && <HomeView />}
          {view === 'chat' && <ChatView />}
          {ModView && <ModView />}
          {view === 'calendar' && <CalendarView />}
          {view === 'mail' && <MailView />}
          {view === 'boards' && <BoardsView />}
          {view === 'dashboards' && <DashboardsView />}
          {view === 'memory' && <MemoryPanel />}
          {view === 'documents' && <DocumentsView />}
          {view === 'docs' && <DocsView />}
          {view === 'activity' && <ActivityView />}
          {view === 'project' && <ProjectView />}
        </>
      )}
      {pageAgentOpen && <PageAgentPanel />}
      {settingsOpen && <SettingsModal />}
      {projectModal && <ProjectModal />}
      <Toasts />
    </div>
  )
}
