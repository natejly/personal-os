import { useEffect, useState } from 'react'
import { Calendar, Mail, CheckSquare, Brain, FolderKanban, Sparkles, RefreshCw, PanelLeftOpen, ExternalLink, Plus, MessageSquare, SlidersHorizontal, X, ListChecks, HardDrive } from 'lucide-react'
import { useStore } from '../store'
import { HOME_MODULES, homeModuleOn } from '../modules'
import AgentInbox from './AgentInbox'
import TodoItem from './TodoItem'
import ProjectChip from './ProjectChip'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { SAFE_MD } from './Message'

function greeting(): string {
  const h = new Date().getHours()
  return h < 5 ? 'Still up?' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
}
const fmtTime = (iso: string, allDay: boolean): string => (allDay ? 'All day' : new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }))
const dayKey = (iso: string): string => new Date(iso.length === 10 ? iso + 'T00:00:00' : iso).toDateString()
const fromName = (s: string | null): string => (s ?? '').replace(/<.*>/, '').replace(/"/g, '').trim() || (s ?? '')
// Google Tasks dues are midnight UTC; take the date part so it doesn't shift a day locally.
const fmtDue = (iso: string): string => new Date(iso.slice(0, 10) + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

export default function HomeView(): JSX.Element {
  const d = useStore((s) => s.dashboard)
  const google = useStore((s) => s.google)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const { toggleSidebar, refreshDashboard, setView, newChat, send, openProject, selectChat, addTodo, setSettingsOpen, refreshRecap } = useStore()
  const recap = useStore((s) => s.recap)
  const recapLoading = useStore((s) => s.recapLoading)
  const settings = useStore((s) => s.settings)
  const saveSettings = useStore((s) => s.saveSettings)
  const [recapOpen, setRecapOpen] = useState(true)
  const [quick, setQuick] = useState('')
  const [busy, setBusy] = useState(false)
  const [customizing, setCustomizing] = useState(false)

  const on = (key: string): boolean => homeModuleOn(settings, key)
  const toggleModule = (key: string): void => {
    void saveSettings({ homeWidgets: { ...(settings.homeWidgets ?? {}), [key]: !on(key) } })
  }

  useEffect(() => { void refreshDashboard() }, [refreshDashboard])

  const brief = async (): Promise<void> => {
    newChat(null)
    await send('Give me my daily brief: check my calendar for today and tomorrow, scan unread email for anything that needs a reply, list my open todos (flag overdue ones), and end with the 3 things I should do first. Be concise and use headers.')
  }
  const refresh = async (): Promise<void> => { setBusy(true); await refreshDashboard(); setBusy(false) }
  const quickAdd = async (): Promise<void> => {
    if (!quick.trim()) return
    await addTodo({ title: quick })
    setQuick('')
  }

  const today = new Date().toDateString()
  const events = d?.calendar ?? []
  const todayEvents = events.filter((e) => dayKey(e.start) === today)
  const laterEvents = events.filter((e) => dayKey(e.start) !== today)

  return (
    <main className="page home">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" aria-label="Show sidebar" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2>Today <span className="muted">{new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}</span></h2>
        <div className="no-drag header-right">
          <button className="icon-btn" title="Refresh" aria-label="Refresh today’s data" onClick={() => void refresh()}><RefreshCw size={15} className={busy ? 'spin' : ''} /></button>
          <div className="home-customize-wrap">
            <button className={`icon-btn ${customizing ? 'on' : ''}`} title="Choose what shows here" onClick={() => setCustomizing((v) => !v)}><SlidersHorizontal size={15} /></button>
            {customizing && (
              <>
                <div className="popover-backdrop" onMouseDown={() => setCustomizing(false)} />
                <div className="home-customize">
                  <h4>Show on Today</h4>
                  {HOME_MODULES.map((m) => (
                    <label key={m.key} className="chip-check-row">
                      <input type="checkbox" checked={on(m.key)} onChange={() => toggleModule(m.key)} />
                      <span>{m.label}</span>
                    </label>
                  ))}
                </div>
              </>
            )}
          </div>
          <button className="primary-btn" onClick={() => void brief()}><Sparkles size={14} /> Brief me</button>
        </div>
      </header>
      <div className="page-body wide">
        <div className="home-hero">
          <h1 role="heading" aria-level={2}>{greeting()}.</h1>
          <div className="quick-ask">
            <MessageSquare size={16} />
            <input placeholder="Ask anything…" value={quick} onChange={(e) => setQuick(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void quickAdd() }
                else if (e.key === 'Enter' && quick.trim()) { e.preventDefault(); const q = quick; setQuick(''); newChat(null); void send(q) }
              }} />
            <button className="ghost-btn" onClick={() => void quickAdd()} disabled={!quick.trim()} title="Add as todo (⌘↵)"><Plus size={13} /> Todo</button>
          </div>
        </div>

        {on('agent') && <AgentInbox />}

        {on('recap') && (recap?.content || recapLoading) && recapOpen && (
          <section className="recap">
            <header>Daily recap <span className="muted small">{recap?.cached ? 'generated earlier today' : 'fresh'}</span>
              <span style={{ flex: 1 }} />
              <button className="icon-btn sm" title="Regenerate" aria-label="Regenerate daily recap" onClick={() => void refreshRecap(true)}><RefreshCw size={13} className={recapLoading ? 'spin' : ''} /></button>
              <button className="icon-btn sm" title="Hide" aria-label="Hide daily recap" onClick={() => setRecapOpen(false)}><X size={13} /></button>
            </header>
            {recapLoading && !recap?.content ? <p className="muted">Writing your recap…</p> : <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={SAFE_MD}>{recap?.content ?? ''}</ReactMarkdown></div>}
          </section>
        )}
        <div className="widgets">
          {on('calendar') && <section className="widget">
            <header><Calendar size={14} /> Calendar {google?.connected && <span className="muted small">next 48h</span>}</header>
            {!google?.connected ? (
              <div className="widget-empty">
                <button className="primary-btn" onClick={() => setSettingsOpen(true)}>Connect Google</button>
              </div>
            ) : d?.errors.calendar ? <p className="msg-error">{d.errors.calendar}</p> : events.length === 0 ? <p className="muted">Nothing scheduled.</p> : (
              <ul className="events">
                {todayEvents.map((e) => (
                  <li key={e.id}><span className="ev-time">{fmtTime(e.start, e.all_day)}</span><span className="ev-title">{e.summary}</span>{e.link && <a href={e.link} target="_blank" rel="noreferrer" className="icon-btn ghost sm" aria-label={`Open “${e.summary}” in Google Calendar`}><ExternalLink size={11} /></a>}</li>
                ))}
                {laterEvents.length > 0 && <li className="ev-sep">Tomorrow</li>}
                {laterEvents.map((e) => (
                  <li key={e.id}><span className="ev-time">{fmtTime(e.start, e.all_day)}</span><span className="ev-title">{e.summary}</span></li>
                ))}
              </ul>
            )}
          </section>}

          {on('todos') && <section className="widget">
            <header><CheckSquare size={14} /> Todos <span className="muted small">{d?.todo_stats.open ?? 0} open{d?.todo_stats.overdue ? ` · ${d.todo_stats.overdue} overdue` : ''}</span><button className="link small" onClick={() => setView('todos')}>all</button></header>
            {(d?.todos.length ?? 0) === 0 ? <p className="muted">All clear.</p> : d!.todos.slice(0, 8).map((t) => <TodoItem key={t.id} todo={t} compact />)}
          </section>}

          {on('inbox') && <section className="widget">
            <header><Mail size={14} /> Inbox {google?.connected && <span className="muted small">unread, 14 days</span>}<button className="link small" onClick={() => setView('mail')}>all</button></header>
            {!google?.connected ? <p className="muted">Connect Google.</p> : d?.errors.gmail ? <p className="msg-error">{d.errors.gmail}</p> : (d?.gmail?.length ?? 0) === 0 ? <p className="muted">Inbox zero.</p> : (
              <ul className="mails">
                {d!.gmail!.slice(0, 8).map((m) => (
                  <li key={m.id} onClick={() => { newChat(null); void send(`Summarize this email and suggest a reply if one is needed. Gmail message id: ${m.id} (subject: ${m.subject})`) }} title="Ask the assistant about this email">
                    <span className="mail-from">{fromName(m.from)}</span>
                    <span className="mail-subject">{m.subject || '(no subject)'}</span>
                    <span className="mail-snippet">{m.snippet}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>}

          {on('gtasks') && <section className="widget">
            <header><ListChecks size={14} /> Google Tasks</header>
            {!google?.connected ? <p className="muted">Connect Google.</p> : d?.errors.tasks ? <p className="msg-error">{d.errors.tasks}</p> : (d?.tasks?.length ?? 0) === 0 ? <p className="muted">No open tasks.</p> : (
              <ul className="events">
                {d!.tasks!.slice(0, 8).map((t) => (
                  <li key={t.id}><span className="ev-title">{t.title || '(untitled)'}</span>{t.due && <span className="muted small">{fmtDue(t.due)}</span>}</li>
                ))}
              </ul>
            )}
          </section>}

          {on('drive') && <section className="widget">
            <header><HardDrive size={14} /> Drive {google?.connected && d?.drive && <span className="muted small">recently modified</span>}</header>
            {!google?.connected ? <p className="muted">Connect Google.</p>
              : google.missing_scopes.some((s) => s.includes('drive')) ? (
                <div className="widget-empty">
                  <p className="muted">Drive needs a fresh sign-in.</p>
                  <button className="primary-btn" onClick={() => setSettingsOpen(true)}>Reconnect Google</button>
                </div>
              ) : d?.errors.drive ? <p className="msg-error">{d.errors.drive}</p> : (d?.drive?.length ?? 0) === 0 ? <p className="muted">No recent files.</p> : (
                <ul className="events">
                  {d!.drive!.slice(0, 8).map((f) => (
                    <li key={f.id}>
                      <span className="ev-title">{f.name}</span>
                      {f.modified && <span className="muted small">{new Date(f.modified).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span>}
                      {f.link && <a href={f.link} target="_blank" rel="noreferrer" className="icon-btn ghost sm"><ExternalLink size={11} /></a>}
                    </li>
                  ))}
                </ul>
              )}
          </section>}

          {on('projects') && <section className="widget">
            <header><FolderKanban size={14} /> Projects</header>
            {(d?.projects.length ?? 0) === 0 ? <p className="muted">No projects yet.</p> : (
              <ul className="proj-list">
                {d!.projects.map((p) => (
                  <li key={p.id} onClick={() => openProject(p.id)}>
                    <span className="project-dot" style={{ background: p.color }} /><span className="ev-title">{p.name}</span>
                    <span className="muted small">{p.stats?.conversations ?? 0} chats · {p.stats?.documents ?? 0} docs</span>
                  </li>
                ))}
              </ul>
            )}
          </section>}

          {on('memories') && <section className="widget">
            <header><Brain size={14} /> Recently learned <button className="link small" onClick={() => setView('memory')}>all</button></header>
            {(d?.recent_memories.length ?? 0) === 0 ? <p className="muted">Nothing yet. Chat with auto-learn on.</p> : (
              <ul className="mem-list">{d!.recent_memories.map((m) => <li key={m.id}>{m.content} <ProjectChip projectId={m.project_id} clickable={false} /></li>)}</ul>
            )}
          </section>}

          {on('chats') && <section className="widget">
            <header><MessageSquare size={14} /> Recent chats</header>
            {(d?.recent_conversations.length ?? 0) === 0 ? <p className="muted">No chats yet.</p> : (
              <ul className="proj-list">{d!.recent_conversations.map((c) => <li key={c.id} onClick={() => void selectChat(c.id)}><span className="ev-title">{c.title}</span><ProjectChip projectId={c.project_id} clickable={false} /></li>)}</ul>
            )}
          </section>}
        </div>
      </div>
    </main>
  )
}
