import { useEffect, useRef } from 'react'
import { X } from 'lucide-react'
import { useStore } from './store'
import { watchSelection } from './lib/pageContext'
import Sidebar from './components/Sidebar'
import ChatView from './components/ChatView'
import DocsView from './components/DocsView'
import MeetingsView from './components/MeetingsView'
import ActivityView from './components/ActivityView'
import ProjectView from './components/ProjectView'
import HomeView from './components/HomeView'
import BoardsView from './components/BoardsView'
import CalendarView from './components/CalendarView'
import MailView from './components/MailView'
import DashboardsView from './components/DashboardsView'
import PendingSends from './components/PendingSends'
import PageAgentPanel from './components/PageAgentPanel'
import LibraryView from './components/LibraryView'
import CoworkView from './components/CoworkView'
import { collectNotices } from './lib/deskNotify'
import { notify } from './lib/notify'
import SettingsModal from './components/SettingsModal'
import ProjectModal from './components/ProjectModal'
import { moduleForView } from './shell/registry'
import Canvas from './canvas/Canvas'
import { useCanvas } from './canvas/store'
import { BackendBanner } from './components/BackendStatus'
import BackendFailed from './components/BackendFailed'
import Onboarding from './components/onboarding/Onboarding'
import { useOnboarding } from './components/onboarding/onboardingStore'
import { accentId } from './lib/accents'
import { api } from './lib/api'

/**
 * One native notification per desk per transition into a state that needs you (a plan, an approval, a
 * question, an interruption, review) or ends the work (done, failed) — the last gap in "hand it a task and go
 * away". It rides an event already flowing (`desk_status`), so there is no poller.
 *
 * The de-duplication is `collectNotices`' key (desk + status + reason), kept here rather than in the store:
 * `desk_status` republishes the same row on every DeskRuntime flush and again with the settled row at the end
 * of a turn, and the store keeps `putDesk` idempotent rather than dropping repeats. A repeated row is free; a
 * repeated banner is not. The first pass only seeds the map — a desk already in `review` at launch is history.
 */
function DeskNotifier(): null {
  const desks = useStore((s) => s.desks)
  // Missing means on, like every other module flag.
  const enabled = useStore((s) => s.settings.deskNotify !== false)
  const seen = useRef<Map<string, string> | null>(null)
  useEffect(() => {
    const seeding = seen.current === null
    const last = (seen.current ??= new Map())
    // Always walked, even when off, so turning the setting on later does not replay what happened meanwhile.
    const notices = collectNotices(last, desks, seeding)
    if (!enabled) return
    for (const n of notices) notifyDesk(n)
  }, [desks, enabled])
  return null
}

/**
 * Main owns the Notification (it survives the window being hidden, shows only when the window is not focused,
 * and a click focuses the window and opens the desk). The renderer's own Notification stays as the fallback for
 * a preload without the bridge, and is skipped while the window has focus.
 */
function notifyDesk(n: { title: string; body: string; deskId: string }): void {
  const bridge = (window.os as unknown as { deskNotify?: (p: { title: string; body: string; deskId?: string }) => void }).deskNotify
  if (bridge) return bridge(n)
  try {
    if (document.hasFocus()) return
  } catch {
    return
  }
  notify(n.title, n.body, { tag: `desk:${n.deskId}`, onClick: () => void useStore.getState().openDesk(n.deskId) })
}

const JOB_SEEN_KEY = 'grain.jobNotifySince'
const readSeen = (): number => {
  try {
    const v = Number(localStorage.getItem(JOB_SEEN_KEY))
    return Number.isFinite(v) && v > 0 ? v : Date.now() / 1000
  } catch {
    return Date.now() / 1000
  }
}
const writeSeen = (t: number): void => {
  try {
    localStorage.setItem(JOB_SEEN_KEY, String(t))
  } catch {
    // Per-viewer convenience only; without it the next check starts from now.
  }
}

/**
 * An OS notification when an unattended job fails, is paused or leaves proposals. Doorbell, not poller: the
 * backend rings `job_finished` on the app topic and this asks /inbox/notify what is new since the last look.
 * Bodies are names and counts only (the backend never sends reply text). It only speaks while the window is
 * hidden; coming back to the window just moves the cursor, because the inbox is then in front of the user.
 */
function JobNotifier(): null {
  const enabled = useStore((s) => s.settings.notifyJobs !== false)
  useEffect(() => {
    if (!enabled) return
    let busy = false
    const check = async (speak: boolean): Promise<void> => {
      if (busy) return
      busy = true
      try {
        const since = readSeen()
        if (!speak) return writeSeen(Date.now() / 1000)
        const events = await api.inboxNotify(since)
        if (events.length) writeSeen(Math.max(...events.map((e) => e.at)))
        if (typeof Notification !== 'function' || Notification.permission === 'denied') return
        for (const e of events) new Notification(e.title, { body: e.body })
      } catch {
        // A notification is never worth a render crash, and a backend that is down has nothing to say.
      } finally {
        busy = false
      }
    }
    const onFinished = (): void => void check(document.hidden)
    const onFocus = (): void => void check(false)
    window.addEventListener('grain-job-finished', onFinished)
    window.addEventListener('focus', onFocus)
    return () => {
      window.removeEventListener('grain-job-finished', onFinished)
      window.removeEventListener('focus', onFocus)
    }
  }, [enabled])
  return null
}

function Toasts(): JSX.Element {
  const toasts = useStore((s) => s.toasts)
  const hold = useStore((s) => s.holdToasts)
  const dismiss = useStore((s) => s.dismissToast)
  // Hovered or focused, the stack keeps still; it resumes only once neither holds it.
  const release = (el: HTMLElement): void => {
    if (!el.matches(':hover') && !el.contains(document.activeElement)) hold(false)
  }
  return (
    <div
      className="toasts"
      role="status"
      aria-live="polite"
      onMouseEnter={() => hold(true)}
      onMouseLeave={(e) => release(e.currentTarget)}
      onFocus={() => hold(true)}
      onBlur={(e) => { const el = e.currentTarget; if (!el.contains(e.relatedTarget as Node | null)) setTimeout(() => release(el)) }}
    >
      {/* Global, not per-view: a send the assistant queued has to be undoable from wherever you are. */}
      <PendingSends />
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}${t.action ? ' with-action' : ''}`} role={t.kind === 'error' ? 'alert' : undefined}>
          <span>{t.text}</span>
          {t.action && <button className="toast-action" onClick={() => { t.action?.run(); dismiss(t.id) }}>{t.action.label}</button>}
          <button className="toast-close" aria-label="Dismiss" onClick={() => dismiss(t.id)}><X size={12} /></button>
        </div>
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
  const accent = useStore((s) => s.settings.accent)
  const inCanvas = useStore((s) => s.view === 'canvas')
  const pageAgentOpen = useStore((s) => s.pageAgentOpen)
  const wizardOpen = useOnboarding((s) => s.open)

  useEffect(() => {
    void init()
  }, [init])
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.dataset.accent = accentId(accent)
  }, [theme, accent])
  // A fresh install (no key, never onboarded) opens the first-run wizard once the backend is up.
  useEffect(() => { if (ready && !backendError) void useOnboarding.getState().check() }, [ready, backendError])
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
  if (backendError) return <BackendFailed message={backendError} />

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
          {view === 'docs' && <DocsView />}
          {view === 'meetings' && <MeetingsView />}
          {view === 'activity' && <ActivityView />}
          {view === 'library' && <LibraryView />}
          {view === 'cowork' && <CoworkView />}
          {view === 'project' && <ProjectView />}
        </>
      )}
      {pageAgentOpen && <PageAgentPanel />}
      {settingsOpen && <SettingsModal />}
      {projectModal && <ProjectModal />}
      <BackendBanner />
      {wizardOpen && <Onboarding />}
      <DeskNotifier />
      <JobNotifier />
      <Toasts />
    </div>
  )
}
