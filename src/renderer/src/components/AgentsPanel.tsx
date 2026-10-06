import { useMemo, useState } from 'react'
import { Check, MessageSquare, Pencil, Plus, Sparkles, Trash2 } from 'lucide-react'
import type { AgentFields, AgentScope, ToolMode } from '@shared/types'
import { useStore } from '../store'
import { api } from '../lib/api'
import { agentText } from '../lib/defText'
import { agentState } from '../lib/mentions'
import AgentHome from './AgentHome'
import Face from './Face'
import { ConfirmDelete } from './SkillsPanel'

const BLANK: AgentFields = { name: '', description: '', model: null, tools: [], skills: [], hue: null, hidden: false, body: '', label: '', boundaries: '', workspace: '', tool_modes: {} }

/** What rides beside the text when a definition is saved. Notes are not here: the agent's Memory tab owns them. */
const scopeOf = (f: AgentFields): AgentScope => ({ label: f.label ?? '', boundaries: f.boundaries ?? '', workspace: f.workspace ?? '', tool_modes: f.tool_modes ?? {} })

/**
 * Library > Agents: roles with a face, instructions, tools and skills. A model can draft one from a line; the user
 * edits and saves it. Chats delegate to approved agents by description (agent_spawn), and Chat talks to one directly.
 */
export default function AgentsPanel(): JSX.Element {
  const { builtin, custom } = useStore((s) => s.agentDefs)
  const { refreshAgentDefs, chatWithAgent, toast } = useStore()
  const agentStatus = useStore((s) => s.agentStatus)
  const [homeId, setHomeId] = useState<string | null>(null)
  const [edit, setEdit] = useState<{ id: string | null; fields: AgentFields } | null>(null)
  const [intent, setIntent] = useState('')
  const [drafting, setDrafting] = useState(false)

  const act = async (fn: () => Promise<unknown>): Promise<void> => {
    try { await fn(); await refreshAgentDefs() } catch (e) { toast((e as Error).message, 'error') }
  }
  const draft = async (): Promise<void> => {
    setDrafting(true)
    try {
      const r = await api.agentDefs.draft(intent)
      if (!r.def) return toast(r.reason || 'Could not draft that.', 'error')
      setEdit({ id: null, fields: r.def })
      setIntent('')
    } catch (e) { toast((e as Error).message, 'error') } finally { setDrafting(false) }
  }

  const home = custom.find((d) => d.id === homeId)
  if (home) return <AgentHome def={home} onBack={() => setHomeId(null)} />

  return (
    <div className="library-panel">
      <p className="muted small">An agent is a role with its own face, instructions, tools and skills. Replies hand work to approved agents whose description fits, and Chat talks to one directly. Editing withdraws the approval.</p>
      <div className="add-row">
        <input placeholder="Describe an agent to draft, e.g. a travel planner that checks my calendar and keeps a packing list" value={intent}
          onChange={(e) => setIntent(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && intent.trim().length >= 4) void draft() }} />
        <button className="primary-btn" disabled={drafting || intent.trim().length < 4} onClick={() => void draft()}><Sparkles size={13} /> {drafting ? 'Drafting…' : 'Draft'}</button>
        <button className="ghost-btn" onClick={() => setEdit({ id: null, fields: BLANK })}><Plus size={13} /> New</button>
      </div>
      {edit && !edit.id && <div className="skill-row"><AgentEditor initial={edit.fields} save={(t, sc) => api.agentDefs.create(t, sc)} onDone={() => { setEdit(null); void refreshAgentDefs() }} /></div>}
      <section className="skill-section">
        <h4>Yours</h4>
        {custom.length === 0 && <div className="empty-hint"><p className="muted small">None yet. Draft one above.</p></div>}
        {custom.map((d) => (
          <div key={d.id} className={`skill-row ${d.approved ? 'approved' : ''}`}>
            <div className="skill-head static">
              <Face name={d.name} hue={d.hue ?? undefined} size={22} />
              <button className="skill-name link" title="Open this agent's page" onClick={() => setHomeId(d.id)}>{d.name}</button>
              {(() => { const a = agentState(agentStatus[d.name]); return a.state === 'idle' ? null : <span className={`agent-status ${a.state}`}>{a.label}</span> })()}
              <span className="skill-desc muted">{d.label || d.description}</span>
              {!d.approved && <small className="muted">unapproved: replies cannot delegate to it</small>}
              <div className="skill-actions no-drag">
                <button className="ghost-btn sm" disabled={!d.approved} title={d.approved ? 'A new chat that speaks as this agent' : 'Approve it first'} onClick={() => chatWithAgent(d.name, d.model)}><MessageSquare size={13} /> Chat</button>
                <button className={d.approved ? 'ghost-btn sm' : 'primary-btn sm'} onClick={() => void act(() => api.agentDefs.approve(d.id, !d.approved))}>
                  {d.approved ? 'Unapprove' : <><Check size={13} /> Approve</>}
                </button>
                <button className="icon-btn ghost" aria-label={`Edit ${d.name}`} onClick={() => setEdit(edit?.id === d.id ? null : { id: d.id, fields: d })}><Pencil size={13} /></button>
                <ConfirmDelete label={d.name} onDelete={() => void act(() => api.agentDefs.delete(d.id))} />
              </div>
            </div>
            {edit?.id === d.id && <AgentEditor initial={edit.fields} save={(t, sc) => api.agentDefs.update(d.id, t, sc)} onDone={() => { setEdit(null); void refreshAgentDefs() }} />}
          </div>
        ))}
      </section>
      <section className="skill-section">
        <h4>Built in</h4>
        {builtin.map((b) => (
          <div key={b.name} className="skill-row"><div className="skill-head static">
            <Face name={b.name} hue={b.hue ?? undefined} size={22} />
            <span className="skill-name">{b.name}</span><span className="skill-desc muted">{b.description}</span>
            <div className="skill-actions no-drag">
              <button className="ghost-btn sm" title="A new chat that speaks as this agent" onClick={() => chatWithAgent(b.name)}><MessageSquare size={13} /> Chat</button>
            </div>
          </div></div>
        ))}
      </section>
    </div>
  )
}

const slug = (s: string): string => s.toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40)

function AgentEditor({ initial, save, onDone }: { initial: AgentFields; save: (text: string, scope: AgentScope) => Promise<unknown>; onDone: () => void }): JSX.Element {
  const [f, setF] = useState<AgentFields>(initial)
  const [err, setErr] = useState('')
  const [filter, setFilter] = useState('')
  const tools = useStore((s) => s.tools)
  const allSkills = useStore((s) => s.skills)
  const skills = useMemo(() => allSkills.filter((x) => x.status === 'approved'), [allSkills])
  const set = (patch: Partial<AgentFields>): void => setF((x) => ({ ...x, ...patch }))
  const toggle = (key: 'tools' | 'skills', v: string): void => set({ [key]: f[key].includes(v) ? f[key].filter((x) => x !== v) : [...f[key], v] })
  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase()
    return tools.filter((t) => t.available && (!q || t.name.includes(q) || t.group.includes(q)))
  }, [tools, filter])
  const go = async (): Promise<void> => {
    try { await save(agentText({ ...f, name: slug(f.name) }), scopeOf(f)); onDone() } catch (e) { setErr((e as Error).message) }
  }
  return (
    <div className="skill-body agent-editor">
      <div className="agent-face-row">
        <Face name={slug(f.name) || 'agent'} hue={f.hue ?? undefined} size={56} />
        <label className="grow">Name<input value={f.name} placeholder="travel-planner" onChange={(e) => set({ name: e.target.value })} /></label>
        <label>Colour
          <span className="hue-pick">
            <input type="range" min={0} max={359} value={f.hue ?? 0} onChange={(e) => set({ hue: Number(e.target.value) })} />
            {f.hue != null && <button className="ghost-btn sm" title="Let the name pick the colour" onClick={() => set({ hue: null })}>Auto</button>}
          </span>
        </label>
      </div>
      <label>Label<input value={f.label ?? ''} maxLength={80} placeholder="Two to four words, e.g. Inbox triage" onChange={(e) => set({ label: e.target.value })} /></label>
      <label>When to hand work to it<input value={f.description} placeholder="One line another agent can match a task against" onChange={(e) => set({ description: e.target.value })} /></label>
      <label>Instructions<textarea rows={6} value={f.body} placeholder="Who it is, what it does, what it must not do, how it reports back." onChange={(e) => set({ body: e.target.value })} /></label>
      <label>Boundaries<textarea rows={3} maxLength={2000} value={f.boundaries ?? ''} placeholder="What it must ask you before doing, and what it never does." onChange={(e) => set({ boundaries: e.target.value })} /></label>
      <label>Working folder<input value={f.workspace ?? ''} placeholder="Optional, e.g. ~/Documents/Taxes" onChange={(e) => set({ workspace: e.target.value })} /></label>
      <details className="agent-pick">
        <summary>Tools <span className="tag">{f.tools.length}</span></summary>
        <input className="agent-filter" placeholder="Filter tools" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <div className="agent-chips">
          {shown.map((t) => (
            <button key={t.name} className={`tag ${f.tools.includes(t.name) ? 'on' : ''}`} title={t.description} onClick={() => toggle('tools', t.name)}>{t.name}</button>
          ))}
        </div>
      </details>
      <details className="agent-pick">
        <summary>Tool settings for this agent <span className="tag">{Object.keys(f.tool_modes ?? {}).length}</span></summary>
        <p className="muted small">Overrides the project's setting for this agent; a chat's own setting still wins. Anything that leaves the app keeps asking.</p>
        {Object.entries(f.tool_modes ?? {}).map(([name, mode]) => (
          <div key={name} className="agent-face-row">
            <code>{name}</code>
            <select value={mode} aria-label={`Mode for ${name}`} onChange={(e) => set({ tool_modes: { ...f.tool_modes, [name]: e.target.value as ToolMode } })}>
              <option value="on">on</option><option value="ask">ask first</option><option value="off">off</option>
            </select>
            <button className="ghost-btn sm" onClick={() => set({ tool_modes: Object.fromEntries(Object.entries(f.tool_modes ?? {}).filter(([k]) => k !== name)) })}>Inherit</button>
          </div>
        ))}
        <select value="" aria-label="Add a tool setting" onChange={(e) => e.target.value && set({ tool_modes: { ...f.tool_modes, [e.target.value]: 'ask' } })}>
          <option value="">Add a tool setting…</option>
          {tools.filter((t) => t.available && !(f.tool_modes ?? {})[t.name]).map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
        </select>
      </details>
      <details className="agent-pick">
        <summary>Skills <span className="tag">{f.skills.length}</span></summary>
        {skills.length === 0 && <p className="muted small">No approved skills yet (Library › Skills).</p>}
        <div className="agent-chips">
          {skills.map((s) => (
            <button key={s.id} className={`tag ${f.skills.includes(s.name) ? 'on' : ''}`} title={s.description} onClick={() => toggle('skills', s.name)}>{s.name}</button>
          ))}
        </div>
      </details>
      <div className="agent-face-row">
        <label>Model<input value={f.model ?? ''} placeholder="default" onChange={(e) => set({ model: e.target.value || null })} /></label>
        <label className="chip-check-row"><input type="checkbox" checked={f.hidden} onChange={(e) => set({ hidden: e.target.checked })} /> Hidden from the roster replies see</label>
      </div>
      {err && <p className="muted small" role="alert">{err}</p>}
      <div className="row-actions">
        <button className="primary-btn sm" disabled={!f.name.trim() || !f.body.trim()} onClick={() => void go()}>Save</button>
        <button className="ghost-btn sm" onClick={onDone}>Cancel</button>
      </div>
    </div>
  )
}
