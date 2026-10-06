import { useEffect, useState } from 'react'
import { Home, Calendar, Mail, Brain, FolderKanban, Sparkles, RefreshCw, ExternalLink, Plus, MessageSquare, SlidersHorizontal, X, ListChecks, HardDrive } from 'lucide-react'
import { useStore } from '../store'
import { PIM_SETTINGS_TAB, pimLabel, pimProvider, pimStatus } from '../lib/pim'
import { mailWatchLines } from '../lib/todayCards'
import { hasModelKey } from '../lib/modelLabel'
import { api } from '../lib/api'
import { HOME_MODULES, homeModuleOn } from '../modules'
import AgentInbox from './AgentInbox'
import { inboxBadge } from '../lib/inboxBadge'
import { moduleHome } from '../shell/registry'
import ProjectChip from './ProjectChip'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { SAFE_MD } from './Message'
import { fenced, lines, usePageContext } from '../lib/pageContext'
import AppSwitcher from './AppSwitcher'
import PlannerPanel from './PlannerPanel'
import { withoutTodoEvents } from './CalendarWeek'
import SidebarToggle from './SidebarToggle'
import { rowButton } from '../lib/rowButton'

function greeting(): string {
  const h = new Date().getHours()
  return h < 5 ? 'Still up?' : h < 12 ? 'Good morning.' : h < 18 ? 'Good afternoon.' : 'Good evening.'
}
const fmtTime = (iso: string, allDay: boolean): string => (allDay ? 'All day' : new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }))
const dayKey = (iso: string): string => new Date(iso.length === 10 ? iso + 'T00:00:00' : iso).toDateString()
const fromName = (s: string | null): string => (s ?? '').replace(/<.*>/, '').replace(/"/g, '').trim() || (s ?? '')
// Google Tasks dues are midnight UTC; take the date part so it doesn't shift a day locally.
const fmtDue = (iso: string): string => new Date(iso.slice(0, 10) + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`

/** One disconnected state for every account-backed card, so each says what it would show and offers the fix. */
function ConnectAccount({ who, what, onConnect }: { who: string; what: string; onConnect: () => void }): JSX.Element {
  return (
    <p className="muted widget-connect">
      Connect {who} to see {what} here. <button className="link" onClick={onConnect}>Connect</button>
    </p>
  )
}

export default function HomeView(): JSX.Element {
  const TodosCard = moduleHome('todos')?.home?.Card
  const HealthCard = moduleHome('health')?.home?.Card
  const d = useStore((s) => s.dashboard)
  const allTodos = useStore((s) => s.todos)
  // Tasks and Drive always come from Google; calendar and mail from the active provider (`pim`).
  const google = useStore((s) => s.google)
  const pim = useStore(pimStatus)
  const pimName = useStore(pimLabel)
  const provider = useStore(pimProvider)
  const tasksSync = useStore((s) => s.tasksSync)
  const { refreshDashboard, setView, newChat, send, askAboutEmail, openProject, selectChat, addTodo, openSettings, refreshRecap, openMemory, toast } = useStore()
  const recap = useStore((s) => s.recap)
  const inbox = useStore((s) => s.agentInbox)
  const recapLoading = useStore((s) => s.recapLoading)
  const settings = useStore((s) => s.settings)
  const saveSettings = useStore((s) => s.saveSettings)
  const [quick, setQuick] = useState('')
  const [busy, setBusy] = useState(false)
  const [customizing, setCustomizing] = useState(false)
  const [watchBusy, setWatchBusy] = useState(false)
  // Whether the first dashboard request has come back, with or without data.
  const [settled, setSettled] = useState(false)

  const on = (key: string): boolean => homeModuleOn(settings, key)
  const inboxNew = useStore((s) => inboxBadge(s.agentInbox))
  const routineDraft = useStore((s) => s.routineDraft)
  const toggleModule = (key: string): void => {
    void saveSettings({ homeWidgets: { ...(settings.homeWidgets ?? {}), [key]: !on(key) } })
  }

  // The ✕ on the recap is the same switch as its row in the customize popover, so hiding it survives
  // a restart; the toast says where it went and offers the way back.
  const hideRecap = (): void => {
    toggleModule('recap')
    toast('Daily recap hidden. “Choose what shows here” brings it back.', 'info', {
      label: 'Undo',
      run: () => void saveSettings({ homeWidgets: { ...(useStore.getState().settings.homeWidgets ?? {}), recap: true } })
    })
  }

  useEffect(() => {
    void refreshDashboard().then(() => setSettled(true))
    if (!useStore.getState().tasksSync) void useStore.getState().refreshTasksSync()
  }, [refreshDashboard])
  // Today left open overnight: coming back to the window re-reads it, and a new day also brings a new recap.
  useEffect(() => {
    let day = new Date().toDateString()
    const onFocus = (): void => {
      void refreshDashboard()
      const now = new Date().toDateString()
      if (now !== day) { day = now; void refreshRecap() }
    }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [refreshDashboard, refreshRecap])

  const brief = async (): Promise<void> => {
    newChat(null)
    // Without an account there is no calendar or mail to read; ask for what Grain can see instead of a run that says so.
    await send(pim?.connected
      ? 'Give me my daily brief: check my calendar for today and tomorrow, scan unread email for anything that needs a reply, list my open todos (flag overdue ones), and end with the 3 things I should do first. Be concise and use headers.'
      : 'Give me my daily brief from my open todos (flag overdue ones) and end with the 3 things I should do first. My calendar and email are not connected, so do not look for them; mention once, at the end, that connecting ${pimName} in Settings adds them. Be concise and use headers.')
  }
  const refresh = async (): Promise<void> => { setBusy(true); await refreshDashboard(); setBusy(false) }
  const rescanMail = async (): Promise<void> => {
    setWatchBusy(true)
    try { await api.mailWatch.refresh(); await refreshDashboard() } catch (e) { useStore.getState().toast((e as Error).message, 'error') } finally { setWatchBusy(false) }
  }
  const quickAdd = async (): Promise<void> => {
    if (!quick.trim()) return
    try {
      await addTodo({ title: quick })
      setQuick('')
      useStore.getState().toast('Added to todos')
    } catch (e) {
      useStore.getState().toast((e as Error).message, 'error')
    }
  }

  const today = new Date().toDateString()
  // A due todo is already in the Todos card; its all-day mirror event would list it twice.
  const events = withoutTodoEvents(d?.calendar ?? [], [...allTodos, ...(d?.todos ?? [])])
  const todayEvents = events.filter((e) => dayKey(e.start) === today)
  const laterEvents = events.filter((e) => dayKey(e.start) !== today)

  // Until the dashboard answers, a card has nothing to count: saying "No projects yet." would be a guess.
  const pending = d === null ? <p className="muted">{settled ? 'Could not load.' : 'Loading…'}</p> : null
  const connect = (): void => openSettings(PIM_SETTINGS_TAB)
  // Without an account, several cards would each say the same "Connect …" line. One strip per account says it
  // once and the cards stay out of the way until they have something to show.
  const googleOff = google !== null && !google.connected
  const pimOff = pim !== null && !pim.connected
  const strips = provider === 'google'
    ? [{ who: 'Google', off: googleOff, keys: ['calendar', 'inbox', 'plan', 'gtasks', 'drive'], what: 'your calendar, unread mail, tasks and recent Drive files' }]
    : [{ who: pimName, off: pimOff, keys: ['calendar', 'inbox', 'plan'], what: 'your calendar and unread mail' },
       { who: 'Google', off: googleOff, keys: ['gtasks', 'drive'], what: 'Google Tasks and recent Drive files' }]
  /** What an account card says instead of its rows: still loading, not connected, or its own error. */
  const gate = (status: typeof google, who: string, what: string, error: string | undefined): JSX.Element | null => {
    if (!status?.connected) return (status === null && pending) || <ConnectAccount who={who} what={what} onConnect={connect} />
    return pending ?? (error ? <p className="msg-error">{error}</p> : null)
  }
  const googleGate = (what: string, error: string | undefined): JSX.Element | null => gate(google, 'Google', what, error)
  const pimGate = (what: string, error: string | undefined): JSX.Element | null => gate(pim, pimName, what, error)

  usePageContext(() => ({
    view: 'home',
    label: 'Today',
    detail: [
      `Today is ${today}.`,
      todayEvents.length ? `Today\u2019s calendar:\n${lines(todayEvents, (e) => `${e.start} — ${e.summary} (\`${e.id}\`)`)}` : 'Nothing on the calendar today.',
      laterEvents.length ? `Coming up:\n${lines(laterEvents, (e) => `${e.start} — ${e.summary}`, 10)}` : '',
      d?.todos?.length ? `Open todos:\n${lines(d.todos, (t) => `${t.title} (\`${t.id}\`${t.due ? `, due ${t.due}` : ''})`)}` : 'No open todos.',
      recap?.content ? `Yesterday\u2019s recap:\n${fenced(recap.content, 1500)}` : '',
      inbox ? (inbox.counts.needs_you ? `Agent inbox, ${inbox.counts.needs_you} waiting on the user:\n${lines([
        ...inbox.needs_you.approvals.map((a) => `approve ${a.tool}${a.job ? ` (${a.job})` : ''}`),
        ...inbox.needs_you.proposals.map((p) => `proposed ${p.tool}${p.source ? ` from ${p.source.name}` : ''}`),
        ...(inbox.needs_you.desks ?? []).map((e) => `desk ${e.desk_title || 'Desk'}: ${e.body || e.kind}`),
        ...(inbox.needs_you.paused_jobs ?? []).map((p) => `paused job ${p.name}: ${p.reason}`),
        ...(inbox.needs_you.elsewhere ?? []).map((q) => `${q.count} ${q.label}`)
      ], (s) => s)}` : 'Agent inbox: nothing is waiting on the user.') : ''
    ].filter(Boolean).join('\n\n'),
    refs: (d?.todos ?? []).slice(0, 20).map((t) => ({ kind: 'todo', id: t.id, name: t.title })),
    hints: ['What should I focus on today?', 'Block time for my todos', 'Anything I am forgetting?']
  }), [d, recap, today, inbox])

  return (
    <main className="page home">
      <header className="page-header drag">
        <SidebarToggle />
        <h2><Home size={16} /> Today <span className="muted">· {new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}</span></h2>
        <div className="no-drag header-right">
          <button className="icon-btn" title="Refresh" aria-label="Refresh today’s data" onClick={() => void refresh()}><RefreshCw size={15} className={busy ? 'spin' : ''} /></button>
          <div className="home-customize-wrap">
            <button className={`icon-btn ${customizing ? 'on' : ''}`} title="Choose what shows here" aria-label="Choose what shows on Today" aria-expanded={customizing} onClick={() => setCustomizing((v) => !v)}><SlidersHorizontal size={15} /></button>
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
          <button className="primary-btn" onClick={() => void brief()} title={pim?.connected ? undefined : `Todos only. Connect ${pimName} in Settings to add mail and calendar.`}><Sparkles size={14} /> Brief me</button>
        </div>
        <AppSwitcher />
      </header>
      <div className="page-body wide">
        <div className="home-hero">
          <h1 role="heading" aria-level={2}>{greeting()}</h1>
          <div className="quick-ask">
            <MessageSquare size={16} />
            <input placeholder="Ask anything…" aria-label="Ask anything" value={quick} onChange={(e) => setQuick(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void quickAdd() }
                else if (e.key === 'Enter' && quick.trim()) { e.preventDefault(); const q = quick; setQuick(''); newChat(null); void send(q) }
              }} />
            <button className="ghost-btn" onClick={() => void quickAdd()} disabled={!quick.trim()} title="Add as a todo instead (⌘↵)" aria-label="Add as a todo instead (⌘↵)"><Plus size={13} /> Todo</button>
          </div>
        </div>

        {/* The sidebar badge points here, so a hidden inbox still shows while it has something to show. */}
        {(on('agent') || inboxNew > 0 || routineDraft) && <AgentInbox />}

        {on('recap') && !hasModelKey(settings) && (
          <p className="muted widget-connect">The daily recap needs a model API key. <button className="link" onClick={() => openSettings('provider')}>Add a key</button></p>
        )}
        {on('recap') && hasModelKey(settings) && (recap?.content || recapLoading) && (
          <section className="recap">
            <header>Daily recap <span className="muted small">{recap?.cached ? 'generated earlier today' : 'fresh'}</span>
              <span className="spacer" />
              <button className="icon-btn sm" title="Regenerate" aria-label="Regenerate daily recap" onClick={() => void refreshRecap(true)}><RefreshCw size={13} className={recapLoading ? 'spin' : ''} /></button>
              <button className="icon-btn sm" title="Hide the daily recap" aria-label="Hide the daily recap" onClick={hideRecap}><X size={13} /></button>
            </header>
            {recapLoading && !recap?.content ? <p className="muted">Writing your recap…</p> : <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={SAFE_MD}>{recap?.content ?? ''}</ReactMarkdown></div>}
          </section>
        )}
        {strips.filter((x) => x.off && x.keys.some(on)).map((x) => (
          <section key={x.who} className="home-connect">
            <p><b>Connect {x.who}</b> to see {x.what} here.</p>
            <button className="ghost-btn" onClick={connect}>Connect {x.who}</button>
          </section>
        ))}
        <div className="widgets">
          {on('calendar') && !pimOff && <section className="widget">
            <header><Calendar size={14} /> Calendar {pim?.connected && <span className="muted small">next 48h</span>}<button className="link small" onClick={() => setView('calendar')}>View all</button></header>
            {pimGate('your calendar', d?.errors.calendar) ?? (events.length === 0 ? <p className="muted">Nothing scheduled.</p> : (
              <ul className="events">
                {todayEvents.map((e) => (
                  <li key={e.id}><span className="ev-time">{fmtTime(e.start, e.all_day)}</span><span className="ev-title">{e.summary}</span>{e.link && <a href={e.link} target="_blank" rel="noreferrer" className="icon-btn ghost sm" aria-label={`Open “${e.summary}” in calendar`}><ExternalLink size={11} /></a>}</li>
                ))}
                {laterEvents.length > 0 && <li className="ev-sep">Tomorrow</li>}
                {laterEvents.map((e) => (
                  <li key={e.id}><span className="ev-time">{fmtTime(e.start, e.all_day)}</span><span className="ev-title">{e.summary}</span></li>
                ))}
              </ul>
            ))}
          </section>}

          {on('todos') && TodosCard && <TodosCard data={d} />}

          {on('health') && HealthCard && <HealthCard data={d} />}

          {on('inbox') && !pimOff && <section className="widget">
            <header><Mail size={14} /> Mail inbox {pim?.connected && <span className="muted small">unread, 14 days</span>}<button className="link small" onClick={() => setView('mail')}>View all</button></header>
            {pimGate('unread mail', d?.errors.gmail) ?? ((d?.gmail?.length ?? 0) === 0 ? <p className="muted">Inbox zero.</p> : (
              <ul className="mails">
                {d!.gmail!.slice(0, 8).map((m) => (
                  <li key={m.id} {...rowButton(() => void askAboutEmail(m.id, m.subject))} title="Ask the assistant about this email">
                    <span className="mail-from">{fromName(m.from)}</span>
                    <span className="mail-subject">{m.subject || '(no subject)'}</span>
                    <span className="mail-snippet">{m.snippet}</span>
                  </li>
                ))}
              </ul>
            ))}
          </section>}

          {on('mailwatch') && d?.mail_watch && <section className="widget">
            <header><Mail size={14} /> Waiting mail
              <button className="link small" onClick={() => { useStore.setState({ mailWatchKind: 'to_reply' }); setView('mail') }}>View all</button>
              <button className="icon-btn sm" title="Re-scan recent threads" aria-label="Re-scan recent threads" onClick={() => void rescanMail()} disabled={watchBusy}><RefreshCw size={13} className={watchBusy ? 'spin' : ''} /></button>
            </header>
            {mailWatchLines(d.mail_watch).length === 0 ? <p className="muted">Nothing waiting.</p> : mailWatchLines(d.mail_watch).map((l) => <p key={l}>{l}</p>)}
          </section>}

          {on('plan') && !pimOff && <section className="widget">
            <header><Calendar size={14} /> Day plan {(d?.planner_blocks?.length ?? 0) > 0 && <span className="muted small">proposed</span>}</header>
            {!pim?.connected ? <ConnectAccount who={pimName} what="a proposed day plan" onConnect={connect} />
              : <PlannerPanel initial={d?.planner_blocks} onApplied={() => void refreshDashboard()} />}
          </section>}

          {on('gtasks') && tasksSync?.config.enabled === false && !googleOff && <section className="widget">
            <header><ListChecks size={14} /> Google Tasks</header>
            {googleGate('Google Tasks', d?.errors.tasks) ?? ((d?.tasks?.length ?? 0) === 0 ? <p className="muted">No open tasks.</p> : (
              <ul className="events">
                {d!.tasks!.slice(0, 8).map((t) => (
                  <li key={t.id}><span className="ev-title">{t.title || '(untitled)'}</span>{t.due && <span className="muted small">{fmtDue(t.due)}</span>}</li>
                ))}
              </ul>
            ))}
          </section>}

          {on('drive') && !googleOff && <section className="widget">
            <header><HardDrive size={14} /> Drive {google?.connected && d?.drive && <span className="muted small">recently modified</span>}</header>
            {/* The scope check comes before the gate: without the scope, the backend's error is only noise. */}
            {google?.connected && google.missing_scopes.some((s) => s.includes('drive')) ? (
              <div className="widget-empty">
                <p className="muted">Drive needs a fresh sign-in.</p>
                <button className="primary-btn" onClick={connect}>Reconnect Google</button>
              </div>
            ) : googleGate('recent Drive files', d?.errors.drive) ?? ((d?.drive?.length ?? 0) === 0 ? <p className="muted">No recent files.</p> : (
              <ul className="events">
                {d!.drive!.slice(0, 8).map((f) => (
                  <li key={f.id}>
                    <span className="ev-title">{f.name}</span>
                    {f.modified && <span className="muted small">{new Date(f.modified).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span>}
                    {f.link && <a href={f.link} target="_blank" rel="noreferrer" className="icon-btn ghost sm" aria-label={`Open “${f.name}” in Google Drive`}><ExternalLink size={11} /></a>}
                  </li>
                ))}
              </ul>
            ))}
          </section>}


          {on('projects') && <section className="widget">
            <header><FolderKanban size={14} /> Projects</header>
            {pending ?? (d!.projects.length === 0 ? <p className="muted">No projects yet.</p> : (
              <ul className="proj-list">
                {d!.projects.map((p) => (
                  <li key={p.id} {...rowButton(() => openProject(p.id))}>
                    <span className="project-dot" style={{ background: p.color }} /><span className="ev-title">{p.name}</span>
                    <span className="muted small">{plural(p.stats?.conversations ?? 0, 'chat')} · {plural((p.stats?.docs ?? 0) + (p.stats?.documents ?? 0), 'file')}</span>
                  </li>
                ))}
              </ul>
            ))}
          </section>}

          {on('memories') && <section className="widget">
            <header><Brain size={14} /> Recently learned <button className="link small" onClick={() => openMemory()}>View all</button></header>
            {pending ?? (d!.recent_memories.length === 0 ? <p className="muted">Nothing yet. Chat with auto-learn on.</p> : (
              <ul className="mem-list">{d!.recent_memories.map((m) => <li key={m.id}>{m.content} <ProjectChip projectId={m.project_id} clickable={false} /></li>)}</ul>
            ))}
          </section>}

          {on('chats') && <section className="widget">
            <header><MessageSquare size={14} /> Recent chats</header>
            {pending ?? (d!.recent_conversations.length === 0 ? <p className="muted">No chats yet.</p> : (
              <ul className="proj-list">{d!.recent_conversations.map((c) => <li key={c.id} {...rowButton(() => void selectChat(c.id))}><span className="ev-title">{c.title}</span><ProjectChip projectId={c.project_id} clickable={false} /></li>)}</ul>
            ))}
          </section>}
        </div>
      </div>
    </main>
  )
}
