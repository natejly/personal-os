import { useEffect } from 'react'
import { useStore } from './store'
import Sidebar from './components/Sidebar'
import ChatView from './components/ChatView'
import MemoryPanel from './components/MemoryPanel'
import DocumentsView from './components/DocumentsView'
import ProjectView from './components/ProjectView'
import HomeView from './components/HomeView'
import TodosView from './components/TodosView'
import BoardsView from './components/BoardsView'
import CalendarView from './components/CalendarView'
import SettingsModal from './components/SettingsModal'
import ProjectModal from './components/ProjectModal'
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
  const { ready, backendError, init, sidebarOpen, settingsOpen, projectModal, view } = useStore()
  const theme = useStore((s) => s.settings.theme)

  useEffect(() => {
    void init()
  }, [init])
  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

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
      {view === 'home' && <HomeView />}
      {view === 'chat' && <ChatView />}
      {view === 'todos' && <TodosView />}
      {view === 'calendar' && <CalendarView />}
      {view === 'boards' && <BoardsView />}
      {view === 'memory' && <MemoryPanel />}
      {view === 'documents' && <DocumentsView />}
      {view === 'project' && <ProjectView />}
      {settingsOpen && <SettingsModal />}
      {projectModal && <ProjectModal />}
      <Toasts />
    </div>
  )
}
