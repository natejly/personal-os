import { useMemo, useState } from 'react'
import { Check, MessageSquare, Pencil, Plus, Sparkles, Trash2 } from 'lucide-react'
import type { AgentFields } from '@shared/types'
import { useStore } from '../store'
import { api } from '../lib/api'
import { agentText } from '../lib/defText'
import Face from './Face'
import { ConfirmDelete } from './SkillsPanel'

const BLANK: AgentFields = { name: '', description: '', model: null, steps: null, tools: [], skills: [], hue: null, hidden: false, body: '' }

/**
 * Library > Agents: roles with a face, instructions, tools and skills. A model can draft one from a line; the user
 * edits and saves it. Chats delegate to approved agents by description (agent_spawn), and Chat talks to one directly.
 */
export default function AgentsPanel(): JSX.Element {
  const { builtin, custom } = useStore((s) => s.agentDefs)
  const { refreshAgentDefs, chatWithAgent, toast } = useStore()
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

  return (
    <div className="library-panel">
      <p className="muted small">An agent is a role with its own face, instructions, tools and skills. Replies hand work to approved agents whose description fits, and Chat talks to one directly. Editing withdraws the approval.</p>
      <div className="add-row">
        <input placeholder="Describe an agent to draft, e.g. a travel planner that checks my calendar and keeps a packing list" value={intent}
          onChange={(e) => setIntent(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && intent.trim().length >= 4) void draft() }} />
        <button className="primary-btn" disabled={drafting || intent.trim().length < 4} onClick={() => void draft()}><Sparkles size={13} /> {drafting ? 'Drafting…' : 'Draft'}</button>
        <button className="ghost-btn" onClick={() => setEdit({ id: null, fields: BLANK })}><Plus size={13} /> New</button>
      </div>
      {edit && !edit.id && <div className="skill-row"><AgentEditor initial={edit.fields} save={(t) => api.agentDefs.create(t)} onDone={() => { setEdit(null); void refreshAgentDefs() }} /></div>}
      <section className="skill-section">
        <h4>Yours</h4>
        {custom.length === 0 && <div className="empty-hint"><p className="muted small">None yet. Draft one above.</p></div>}
        {custom.map((d) => (
          <div key={d.id} className={`skill-row ${d.approved ? 'approved' : ''}`}>
            <div className="skill-head static">
              <Face name={d.name} hue={d.hue ?? undefined} size={22} />
              <span className="skill-name">{d.name}</span>
              <span className="skill-desc muted">{d.description}</span>
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
            {edit?.id === d.id && <AgentEditor initial={edit.fields} save={(t) => api.agentDefs.update(d.id, t)} onDone={() => { setEdit(null); void refreshAgentDefs() }} />}
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

function AgentEditor({ initial, save, onDone }: { initial: AgentFields; save: (text: string) => Promise<unknown>; onDone: () => void }): JSX.Element {
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
    try { await save(agentText({ ...f, name: slug(f.name) })); onDone() } catch (e) { setErr((e as Error).message) }
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
      <label>When to hand work to it<input value={f.description} placeholder="One line another agent can match a task against" onChange={(e) => set({ description: e.target.value })} /></label>
      <label>Instructions<textarea rows={6} value={f.body} placeholder="Who it is, what it does, what it must not do, how it reports back." onChange={(e) => set({ body: e.target.value })} /></label>
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
        <label>Tool rounds<input type="number" min={1} max={60} value={f.steps ?? ''} placeholder="default" onChange={(e) => set({ steps: e.target.value ? Number(e.target.value) : null })} /></label>
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
