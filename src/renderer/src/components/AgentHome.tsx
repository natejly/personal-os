import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowLeft, MessageSquare, Plus } from 'lucide-react'
import type { AgentDef, AgentHomeData } from '@shared/types'
import { useStore } from '../store'
import { api } from '../lib/api'
import { agentState } from '../lib/mentions'
import Face from './Face'
import { JobRow, NewTask } from './AgentInbox'

type Tab = 'chats' | 'routines' | 'skills' | 'memory' | 'activity'
const TABS: { key: Tab; label: string }[] = [
  { key: 'chats', label: 'Chats' }, { key: 'routines', label: 'Routines' }, { key: 'skills', label: 'Skills' },
  { key: 'memory', label: 'Memory' }, { key: 'activity', label: 'Activity' }
]
const stamp = (t: number): string => new Date(t * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

/**
 * One agent's page (Library > Agents > click a row): its status, the chats bound to it, its routines (jobs that run as it,
 * still proposal-only), the approved skills it carries, a notes field it always sees, and what it did last.
 */
export default function AgentHome({ def, onBack }: { def: AgentDef; onBack: () => void }): JSX.Element {
  const { chatWithAgent, selectChat, refreshAgentDefs, refreshJobs, toast } = useStore()
  const status = useStore((s) => s.agentStatus[def.name])
  const allJobs = useStore((s) => s.jobs)
  const allSkills = useStore((s) => s.skills)
  const [tab, setTab] = useState<Tab>('chats')
  const [home, setHome] = useState<AgentHomeData | null>(null)
  const [adding, setAdding] = useState(false)
  const routines = useMemo(() => allJobs.filter((j) => j.agent_id === def.id), [allJobs, def.id])
  const skills = useMemo(() => allSkills.filter((s) => s.status === 'approved'), [allSkills])
  const [notes, setNotes] = useState(def.notes ?? '')
  useEffect(() => setNotes(def.notes ?? ''), [def.notes])
  const { state, label } = agentState(status)

  const load = useCallback(async () => {
    try { setHome(await api.agentDefs.home(def.id)) } catch (e) { toast((e as Error).message, 'error') }
  }, [def.id, toast])
  // Re-read when the tab changes: chats and runs move while another view is open.
  useEffect(() => { void load(); void refreshJobs() }, [load, tab, refreshJobs])

  const saveScope = async (scope: Parameters<typeof api.agentDefs.scope>[1]): Promise<void> => {
    try { await api.agentDefs.scope(def.id, scope); await refreshAgentDefs() } catch (e) { toast((e as Error).message, 'error') }
  }
  const toggleSkill = (name: string): void => {
    void saveScope({ skills: def.skills.includes(name) ? def.skills.filter((x) => x !== name) : [...def.skills, name] })
  }

  return (
    <div className="library-panel agent-home">
      <div className="agent-home-head">
        <button className="ghost-btn sm" onClick={onBack}><ArrowLeft size={13} /> Agents</button>
        <Face name={def.name} hue={def.hue ?? undefined} size={44} />
        <div className="agent-home-title">
          <strong>{def.name}</strong>
          <span className="muted small">{def.label || def.description}</span>
        </div>
        <span className={`agent-status ${state}`} title="From this agent's chats and routines">{label}</span>
        <button className="primary-btn sm" disabled={!def.approved} title={def.approved ? undefined : 'Approve it first'} onClick={() => chatWithAgent(def.name, def.model)}>
          <MessageSquare size={13} /> New chat as {def.name}
        </button>
      </div>
      {!def.approved && <p className="muted small">Unapproved: it cannot chat, run routines or take delegated work until you approve it in the list.</p>}
      {def.boundaries && <p className="muted small agent-boundaries"><strong>Boundaries:</strong> {def.boundaries}</p>}
      <div className="library-tabs tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? 'active' : ''} onClick={() => setTab(t.key)}>
            {t.label}{t.key === 'routines' && routines.length > 0 && <span className="count">{routines.length}</span>}
          </button>
        ))}
      </div>

      {tab === 'chats' && (
        <section>
          {home && home.chats.length === 0 && <p className="muted small">No chats yet. Start one above, or type @{def.name} in any chat.</p>}
          <ul className="agent-home-list">
            {home?.chats.map((c) => (
              <li key={c.id}><button className="link" onClick={() => void selectChat(c.id)}>{c.title || 'Untitled'}</button><span className="muted small">{stamp(c.updated_at)}</span></li>
            ))}
          </ul>
        </section>
      )}

      {tab === 'routines' && (
        <section>
          <p className="muted small">Scheduled work this agent does on its own. Each run uses its instructions, skills, boundaries and tool settings, and anything that leaves the app comes back as a proposal.</p>
          {routines.length === 0 && !adding && <p className="muted small">No routines yet.</p>}
          {routines.length > 0 && <ul>{routines.map((j) => <JobRow key={j.id} job={j} />)}</ul>}
          {adding
            ? <NewTask agentId={def.id} onDone={() => { setAdding(false); void refreshJobs() }} />
            : <button className="ghost-btn sm" disabled={!def.approved} onClick={() => setAdding(true)}><Plus size={13} /> New routine…</button>}
        </section>
      )}

      {tab === 'skills' && (
        <section className="agent-editor">
          <p className="muted small">The approved skills this agent carries into every chat and routine.</p>
          {skills.length === 0 && <p className="muted small">No approved skills yet (Library › Skills).</p>}
          <div className="agent-chips">
            {skills.map((s) => (
              <button key={s.id} className={`tag ${def.skills.includes(s.name) ? 'on' : ''}`} title={s.description} aria-pressed={def.skills.includes(s.name)}
                onClick={() => toggleSkill(s.name)}>{s.name}</button>
            ))}
          </div>
        </section>
      )}

      {tab === 'memory' && (
        <section className="agent-editor">
          <label>What this agent should remember
            <textarea rows={6} maxLength={4000} value={notes} placeholder="Names, preferences, standing context. It sees this at the start of every chat and routine."
              onChange={(e) => setNotes(e.target.value)} />
          </label>
          <div className="row-actions">
            <button className="primary-btn sm" disabled={notes === (def.notes ?? '')} onClick={() => void saveScope({ notes })}>Save</button>
          </div>
        </section>
      )}

      {tab === 'activity' && (
        <section>
          {home && home.runs.length === 0 && <p className="muted small">Nothing yet.</p>}
          <ul className="agent-home-list">
            {home?.runs.map((r) => (
              <li key={r.run_id}>
                <span className={`agent-status ${r.status === 'running' ? 'working' : r.status === 'awaiting_approval' ? 'needs-you' : 'idle'}`}>{r.kind === 'job' ? 'routine' : 'chat'} · {r.status}</span>
                {r.conversation_id
                  ? <button className="link" onClick={() => void selectChat(r.conversation_id as string)}>{r.title || 'Untitled'}</button>
                  : <span>{r.title || 'Untitled'}</span>}
                <span className="muted small">{stamp(r.started_at)}</span>
                {r.error && <span className="muted small" title={r.error}>{r.error.slice(0, 60)}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
