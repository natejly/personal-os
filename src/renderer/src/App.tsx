import { useEffect, useRef } from 'react'
import { useStore } from './store'
import Sidebar from './components/Sidebar'
import ChatView from './components/ChatView'
import MemoryPanel from './components/MemoryPanel'
import DocumentsView from './components/DocumentsView'
import DocsView from './components/DocsView'
import ProjectView from './components/ProjectView'
import HomeView from './components/HomeView'
import TodosView from './components/TodosView'
import BoardsView from './components/BoardsView'
import CalendarView from './components/CalendarView'
import MailView from './components/MailView'
import DashboardsView from './components/DashboardsView'
import SettingsModal from './components/SettingsModal'
import ProjectModal from './components/ProjectModal'
import Canvas from './canvas/Canvas'
import { useCanvas } from './canvas/store'
import { AlertTriangle } from 'lucide-react'

function Toasts(): JSX.Element {
  const toasts = useStore((s) => s.toasts)
  return (
    <div className="toasts">
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
  const theme = useStore((s) => s.settings.theme)
  const mode = useStore((s) => s.mode)

  useEffect(() => {
    void init()
  }, [init])
  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])
  // `load()` is also what registers the canvas store's menu, bus and pop-out listeners. Every
  // widget filters client-side, so canvas mode loads each shared dataset once at the widest scope.
  const entered = useRef(false)
  useEffect(() => {
    if (mode !== 'canvas') {
      // Leaving canvas mode unmounts the plane, so the 400 ms layout debounce would never fire.
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
  }, [mode])

  if (!ready) return <div className="app loading" />
  if (backendError) {
    return (
      <div className="app loading">
        <div className="backend-error drag">
          <AlertTriangle size={28} />
          <h2>Backend not running</h2>
          <p>Personal OS could not start its Python backend.</p>
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
    <div className={`app ${sidebarOpen ? '' : 'sidebar-collapsed'}`}>
      <Sidebar />
      {mode === 'canvas' ? (
        <Canvas />
      ) : (
        <>
          {view === 'home' && <HomeView />}
          {view === 'chat' && <ChatView />}
          {view === 'todos' && <TodosView />}
          {view === 'calendar' && <CalendarView />}
          {view === 'mail' && <MailView />}
          {view === 'boards' && <BoardsView />}
          {view === 'dashboards' && <DashboardsView />}
          {view === 'memory' && <MemoryPanel />}
          {view === 'documents' && <DocumentsView />}
          {view === 'docs' && <DocsView />}
          {view === 'project' && <ProjectView />}
        </>
      )}
      {settingsOpen && <SettingsModal />}
      {projectModal && <ProjectModal />}
      <Toasts />
    </div>
  )
}
