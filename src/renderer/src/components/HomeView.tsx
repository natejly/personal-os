import { useEffect, useState } from 'react'
import { Home, Calendar, Mail, Brain, FolderKanban, Sparkles, RefreshCw, PanelLeftOpen, ExternalLink, Plus, MessageSquare, Mic, SlidersHorizontal, X, ListChecks, HardDrive } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import { formatOffset, offerableCandidates } from '../lib/transcript'
import { HOME_MODULES, homeModuleOn } from '../modules'
import AgentInbox from './AgentInbox'
import HomeCowork from './HomeCowork'
import type { Meeting, MeetingCandidate } from '@shared/types'
import { moduleHome } from '../shell/registry'
import ProjectChip from './ProjectChip'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { SAFE_MD } from './Message'
import { lines, usePageContext } from '../lib/pageContext'
import AppSwitcher from './AppSwitcher'

function greeting(): string {
  const h = new Date().getHours()
  return h < 5 ? 'Still up?' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
}
const fmtTime = (iso: string, allDay: boolean): string => (allDay ? 'All day' : new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }))
const dayKey = (iso: string): string => new Date(iso.length === 10 ? iso + 'T00:00:00' : iso).toDateString()
const fromName = (s: string | null): string => (s ?? '').replace(/<.*>/, '').replace(/"/g, '').trim() || (s ?? '')
// Google Tasks dues are midnight UTC; take the date part so it doesn't shift a day locally.
const fmtDue = (iso: string): string => new Date(iso.slice(0, 10) + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
/** Meeting timestamps are epoch SECONDS, not the ISO strings the calendar rows carry. */
const fmtClock = (secs: number): string => new Date(secs * 1000).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
const fmtLength = (ms: number): string => (ms < 60_000 ? `${Math.max(1, Math.round(ms / 1000))}s` : `${Math.round(ms / 60_000)} min`)

/** How often the calendar nudge is re-asked. The backend caches it for 60s, so this is its period. */
const SUGGEST_MS = 60_000

/**
 * Today's meetings, whatever is waiting to be reviewed, and the calendar events the backend is
 * offering to take notes on.
 *
 * Its own component so the 60s poll lives and dies with the card rather than with Today. Nothing
 * here starts a recording on its own: a candidate is an offer with a button, and the one headless
 * path (`autoRecord`) is a setting the user has to turn on in the Meetings panel.
 */
function MeetingsCard(): JSX.Element {
  // Today's list is deliberately NOT the store's `meetings` array: that one is the Meetings rail's
  // search result, so a query still sitting in the rail's box would silently filter Today - with no
  // search box on this page to explain the gap, and with a filtered-out meeting costing its
  // candidate the "take notes" button. This card owns an unfiltered copy instead.
  const [meetings, setMeetings] = useState<Meeting[]>([])
  const dataScope = useStore((s) => s.dataScope)
  const meetingsPending = useStore((s) => s.meetingsPending)
  const meetingStatus = useStore((s) => s.meetingStatus)
  const meetingBusy = useStore((s) => s.meetingBusy)
  const openMeeting = useStore((s) => s.openMeeting)
  const startRecording = useStore((s) => s.startRecording)
  const recordCandidate = useStore((s) => s.recordCandidate)
  const setView = useStore((s) => s.setView)
  const [candidates, setCandidates] = useState<MeetingCandidate[]>([])

  useEffect(() => {
    void api.meetings.list(dataScope, '').then(setMeetings).catch(() => undefined)
  }, [dataScope, meetingStatus?.active?.meeting_id, meetingsPending])
  useEffect(() => {
    // `/meetings/suggest` answers [] rather than erroring when Google is unconnected, so there is
    // nothing to guard on here and a failure just leaves the offer list empty.
    const ask = (): void => void api.meetings.suggest().then(setCandidates).catch(() => undefined)
    ask()
    const timer = setInterval(ask, SUGGEST_MS)
    return () => clearInterval(timer)
  }, [])

  const today = new Date().toDateString()
  const todays = meetings.filter((m) => {
    const at = m.started_at ?? m.scheduled_start
    return at !== null && new Date(at * 1000).toDateString() === today
  })
  const active = meetingStatus?.active ?? null
  // Filtered on the meeting's STATE, not merely on whether it is the live one: /meetings/suggest
  // keeps offering an event for its whole window, so a call that was already recorded and stopped
  // comes back with its meeting id, and starting that id again restarts the segment counter in the
  // same directory and overwrites the beginning of the recording.
  const offers = offerableCandidates(candidates, meetings, active?.meeting_id ?? null)
  // The backend refuses `start` while the master switch is off, so the offer says why up front.
  const recorderOff = meetingStatus !== null && !meetingStatus.config.enabled
  const OFF_TITLE = 'The meeting recorder is off. Turn it on in the Meetings panel.'

  return (
    <section className="widget">
      <header>
        <Mic size={14} /> Meetings
        {meetingsPending > 0 && <span className="muted small">{meetingsPending} awaiting review</span>}
        <button className="link small" onClick={() => setView('meetings')}>all</button>
      </header>

      {active && (
        <ul className="events">
          <li onClick={() => setView('meetings')} title="Open the meeting that is recording">
            <span className="ev-time">{formatOffset(active.elapsed_ms / 1000)}</span>
            <span className="ev-title">Recording now</span>
          </li>
        </ul>
      )}

      {offers.length > 0 && (
        <ul className="events">
          {offers.map((c) => (
            <li key={`${c.calendar_id}:${c.event_id}`}>
              <span className="ev-time">{fmtTime(c.start, false)}</span>
              <span className="ev-title">{c.title || '(untitled event)'}</span>
              {/* Navigate first: the consent modal is hosted by the Meetings view, so a first-ever
                  recording started from here would otherwise gate on a dialog with nowhere to render. */}
              <button className="link small" disabled={meetingBusy || active !== null || recorderOff}
                title={recorderOff ? OFF_TITLE : undefined}
                onClick={() => { setView('meetings'); void recordCandidate(c) }}>
                take notes
              </button>
            </li>
          ))}
        </ul>
      )}

      {todays.length > 0 && (
        <ul className="events">
          {todays.map((m) => {
            const at = m.started_at ?? m.scheduled_start
            return (
              <li key={m.id} onClick={() => void openMeeting(m.id)} title="Open these notes">
                <span className="ev-time">{at !== null ? fmtClock(at) : ''}</span>
                <span className="ev-title">{m.title || 'Untitled meeting'}</span>
                <span className="muted small">
                  {m.duration_ms > 0 ? fmtLength(m.duration_ms) : m.status}
                  {m.has_pending ? ' · review' : ''}
                </span>
              </li>
            )
          })}
        </ul>
      )}

      {!active && offers.length === 0 && todays.length === 0 && (
        <div className="widget-empty">
          <p className="muted">No meetings today.</p>
          <button className="primary-btn" disabled={meetingBusy || recorderOff}
            title={recorderOff ? OFF_TITLE : undefined}
            onClick={() => { setView('meetings'); void startRecording() }}>
            <Mic size={14} /> Record one
          </button>
        </div>
      )}
    </section>
  )
}

const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`

/** One disconnected state for every Google card, so each says what it would show and offers the fix. */
function ConnectGoogle({ what, onConnect }: { what: string; onConnect: () => void }): JSX.Element {
  return (
    <p className="muted widget-connect">
      Connect Google to see {what} here. <button className="link" onClick={onConnect}>Connect</button>
    </p>
  )
}

export default function HomeView(): JSX.Element {
  const TodosCard = moduleHome('todos')?.home?.Card
  const d = useStore((s) => s.dashboard)
  const google = useStore((s) => s.google)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const { toggleSidebar, refreshDashboard, setView, newChat, send, openProject, selectChat, addTodo, setSettingsOpen, refreshRecap, openMemory } = useStore()
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

  usePageContext(() => ({
    view: 'home',
    label: 'Today',
    detail: [
      `Today is ${today}.`,
      todayEvents.length ? `Today\u2019s calendar:\n${lines(todayEvents, (e) => `${e.start} — ${e.summary} (\`${e.id}\`)`)}` : 'Nothing on the calendar today.',
      laterEvents.length ? `Coming up:\n${lines(laterEvents, (e) => `${e.start} — ${e.summary}`, 10)}` : '',
      d?.todos?.length ? `Open todos:\n${lines(d.todos, (t) => `${t.title} (\`${t.id}\`${t.due ? `, due ${t.due}` : ''})`)}` : 'No open todos.',
      recap?.content ? `Yesterday\u2019s recap:\n${recap.content.slice(0, 1500)}` : ''
    ].filter(Boolean).join('\n\n'),
    refs: (d?.todos ?? []).slice(0, 20).map((t) => ({ kind: 'todo', id: t.id, name: t.title })),
    hints: ['What should I focus on today?', 'Block time for my todos', 'Anything I am forgetting?']
  }), [d, recap, today])

  return (
    <main className="page home">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" aria-label="Show sidebar" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><Home size={16} /> Today <span className="muted">· {new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}</span></h2>
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
        <AppSwitcher />
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
        {on('cowork') && <HomeCowork />}

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
              <ConnectGoogle what="your calendar" onConnect={() => setSettingsOpen(true)} />
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

          {on('todos') && TodosCard && <TodosCard data={d} />}

          {on('inbox') && <section className="widget">
            <header><Mail size={14} /> Inbox {google?.connected && <span className="muted small">unread, 14 days</span>}<button className="link small" onClick={() => setView('mail')}>View all</button></header>
            {!google?.connected ? <ConnectGoogle what="unread mail" onConnect={() => setSettingsOpen(true)} /> : d?.errors.gmail ? <p className="msg-error">{d.errors.gmail}</p> : (d?.gmail?.length ?? 0) === 0 ? <p className="muted">Inbox zero.</p> : (
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
            {!google?.connected ? <ConnectGoogle what="Google Tasks" onConnect={() => setSettingsOpen(true)} /> : d?.errors.tasks ? <p className="msg-error">{d.errors.tasks}</p> : (d?.tasks?.length ?? 0) === 0 ? <p className="muted">No open tasks.</p> : (
              <ul className="events">
                {d!.tasks!.slice(0, 8).map((t) => (
                  <li key={t.id}><span className="ev-title">{t.title || '(untitled)'}</span>{t.due && <span className="muted small">{fmtDue(t.due)}</span>}</li>
                ))}
              </ul>
            )}
          </section>}

          {on('drive') && <section className="widget">
            <header><HardDrive size={14} /> Drive {google?.connected && d?.drive && <span className="muted small">recently modified</span>}</header>
            {!google?.connected ? <ConnectGoogle what="recent Drive files" onConnect={() => setSettingsOpen(true)} />
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

          {on('meetings') && <MeetingsCard />}

          {on('projects') && <section className="widget">
            <header><FolderKanban size={14} /> Projects</header>
            {(d?.projects.length ?? 0) === 0 ? <p className="muted">No projects yet.</p> : (
              <ul className="proj-list">
                {d!.projects.map((p) => (
                  <li key={p.id} onClick={() => openProject(p.id)}>
                    <span className="project-dot" style={{ background: p.color }} /><span className="ev-title">{p.name}</span>
                    <span className="muted small">{plural(p.stats?.conversations ?? 0, 'chat')} · {plural(p.stats?.documents ?? 0, 'doc')}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>}

          {on('memories') && <section className="widget">
            <header><Brain size={14} /> Recently learned <button className="link small" onClick={() => openMemory()}>View all</button></header>
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
