import { useEffect, useState } from 'react'
import { AlertTriangle, Check, ChevronDown, ChevronRight, Eye, Plus, ShieldAlert, Sparkles, Trash2, Undo2, X } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import type { Skill, SkillDraft, SkillFinding, SkillPreview } from '@shared/types'
import ProjectChip from './ProjectChip'

/** Why a procedure exists, in the user's terms. 'proposed' is reserved for the assistant asking directly. */
const SOURCE_LABEL: Record<Skill['source'], string> = {
  induced: 'learned from a chat',
  proposed: 'suggested by the assistant',
  user: 'written by you'
}

const ORDER: Skill['status'][] = ['candidate', 'approved', 'rejected']
const SECTION: Record<Skill['status'], { title: string; blurb: string }> = {
  candidate: { title: 'Waiting for you', blurb: 'Nothing here is in use. Read it, edit it if you like, then approve or discard.' },
  approved: { title: 'In use', blurb: 'Injected into chats that have procedures turned on, as reference material the assistant may follow.' },
  rejected: { title: 'Discarded', blurb: 'Kept so the same suggestion is recognisable if it comes back.' }
}

const EMPTY: SkillDraft = { name: '', description: '', procedure: '' }

/** The backend's lint, on a debounce, for one draft. Both the checks and their wording come from the
 *  server, so what the editor shows is the same thing the approval route will decide on. */
function useLint(draft: SkillDraft, skillId?: string): { findings: SkillFinding[]; blocked: boolean } {
  const [findings, setFindings] = useState<SkillFinding[]>([])
  const { name, description, procedure } = draft
  useEffect(() => {
    if (!name.trim() && !procedure.trim()) {
      setFindings([])
      return
    }
    let live = true
    const t = setTimeout(() => {
      api.skills
        .lint({ name, description, procedure, skill_id: skillId })
        .then((r) => { if (live) setFindings(r.findings) })
        .catch(() => undefined)
    }, 350)
    return () => { live = false; clearTimeout(t) }
  }, [name, description, procedure, skillId])
  return { findings, blocked: findings.some((f) => f.level === 'error') }
}

const RANK: Record<SkillFinding['level'], number> = { error: 0, warn: 1 }

function Findings({ findings }: { findings: SkillFinding[] }): JSX.Element | null {
  if (!findings.length) return null
  return (
    <ul className="skill-findings">
      {[...findings].sort((a, b) => RANK[a.level] - RANK[b.level]).map((f, i) => (
        <li key={`${f.code}-${i}`} className={f.level}>
          {f.level === 'error' ? <ShieldAlert size={13} /> : <AlertTriangle size={13} />}
          <div>
            <b>{f.message}</b>
            {f.excerpt && <code>{f.excerpt}</code>}
            {f.hint && <small className="muted">{f.hint}</small>}
          </div>
          <span className="tag">{f.field}</span>
        </li>
      ))}
    </ul>
  )
}

/** Name, trigger and steps, with the lint under them. Used for a new procedure and for editing one. */
function SkillFields({ value, onChange, skillId }: {
  value: SkillDraft
  onChange: (next: SkillDraft) => void
  skillId?: string
}): JSX.Element {
  const { findings } = useLint(value, skillId)
  return (
    <>
      <label>Name<input value={value.name} placeholder="Weekly review" onChange={(e) => onChange({ ...value, name: e.target.value })} /></label>
      <label>When it applies<input value={value.description} placeholder="when I ask for a weekly review" onChange={(e) => onChange({ ...value, description: e.target.value })} /></label>
      <label>Procedure
        <textarea rows={10} value={value.procedure}
          placeholder={'1. Pull this week\'s done todos.\n2. Check the calendar for what slipped.\n3. Draft the summary as bullets.'}
          onChange={(e) => onChange({ ...value, procedure: e.target.value })} />
      </label>
      <Findings findings={findings} />
    </>
  )
}

function SkillRow({ skill }: { skill: Skill }): JSX.Element {
  const { updateSkill, deleteSkill } = useStore()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<SkillDraft | null>(null)

  const edit = draft ?? { name: skill.name, description: skill.description, procedure: skill.procedure }
  const dirty = draft !== null && (draft.name !== skill.name || draft.description !== skill.description || draft.procedure !== skill.procedure)
  // The same rule as the backend's gate, in the same place the user is typing: a candidate may say
  // anything because a candidate is inert, but text that is (or is about to be) in use may not claim
  // authority. Saving an edit to a live skill is therefore blocked exactly when approving it would be.
  const { blocked } = useLint(edit, skill.id)
  const save = async (): Promise<void> => {
    if (dirty) await updateSkill(skill.id, draft!)
    setDraft(null)
  }

  return (
    <div className={`skill-row ${skill.status}`}>
      <div className="skill-head" onClick={() => setOpen(!open)} role="button" tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter') setOpen(!open) }}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="skill-name">{skill.name}</span>
        <span className="skill-desc muted">{skill.description}</span>
        <ProjectChip projectId={skill.project_id} showPersonal />
        <small className="muted">{SOURCE_LABEL[skill.source]}</small>
        <div className="skill-actions no-drag" onClick={(e) => e.stopPropagation()}>
          {skill.status !== 'approved' && (
            <button className="primary-btn small" disabled={blocked}
              title={blocked ? 'This procedure claims authority over what the assistant may do — open it to see what to cut' : 'Let the assistant use this procedure'}
              onClick={() => void updateSkill(skill.id, { status: 'approved' })}><Check size={13} /> Approve</button>
          )}
          {skill.status === 'approved' && (
            <button className="small" title="Stop injecting this procedure"
              onClick={() => void updateSkill(skill.id, { status: 'candidate' })}><Undo2 size={13} /> Revoke</button>
          )}
          {skill.status === 'candidate' && (
            <button className="small" title="Discard" onClick={() => void updateSkill(skill.id, { status: 'rejected' })}><X size={13} /></button>
          )}
          <button className="icon-btn ghost danger" aria-label={`Delete ${skill.name}`} onClick={() => void deleteSkill(skill.id)}><Trash2 size={13} /></button>
        </div>
      </div>
      {open && (
        <div className="skill-body">
          <SkillFields value={edit} onChange={setDraft} skillId={skill.id} />
          <div className="row-actions">
            <button className="primary-btn small" disabled={!dirty || (skill.status === 'approved' && blocked)} onClick={() => void save()}>Save</button>
            {dirty && <button className="small" onClick={() => setDraft(null)}>Cancel</button>}
            {skill.status === 'approved' && !blocked && <span className="muted small">Edits take effect in the next reply.</span>}
            {skill.status === 'approved' && blocked && <span className="blocked small">This is in use, so it cannot be saved while it claims authority.</span>}
            {skill.status !== 'approved' && blocked && <span className="blocked small">Fix the blocking finding above and this can be approved.</span>}
          </div>
        </div>
      )}
    </div>
  )
}

/** The text the assistant is actually shown, from the same function the chat calls. */
function InjectedPreview(): JSX.Element {
  const projects = useStore((s) => s.projects)
  const [scope, setScope] = useState('personal')
  const [open, setOpen] = useState(false)
  const [p, setP] = useState<SkillPreview | null>(null)
  const skills = useStore((s) => s.skills)

  useEffect(() => {
    if (!open) return
    let live = true
    api.skills.preview(scope).then((r) => { if (live) setP(r) }).catch(() => undefined)
    return () => { live = false }
  }, [open, scope, skills])

  return (
    <div className="skill-preview-box">
      <div className="add-row">
        <button className="small" onClick={() => setOpen(!open)}><Eye size={14} /> {open ? 'Hide' : 'Show'} what the assistant sees</button>
        {open && (
          <label className="inline">In
            <select value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="personal">a personal chat</option>
              {projects.map((pr) => <option key={pr.id} value={pr.id}>a chat in {pr.name}</option>)}
            </select>
          </label>
        )}
        {open && p && <span className="muted small">≈{p.tokens_estimate} tokens{p.omitted.length ? ` · ${p.omitted.length} approved procedure(s) do not fit and are left out: ${p.omitted.map((o) => o.name).join(', ')}` : ''}</span>}
      </div>
      {open && (
        <pre className="skill-injected">{p?.block || 'Nothing is approved in this scope, so no procedures are injected.'}</pre>
      )}
    </div>
  )
}

export default function SkillsPanel(): JSX.Element {
  const skills = useStore((s) => s.skills)
  const { createSkill, toast } = useStore()
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState<SkillDraft>(EMPTY)
  const [intent, setIntent] = useState('')
  const [drafting, setDrafting] = useState(false)

  const add = async (): Promise<void> => {
    if (!form.name.trim()) return
    await createSkill(form)
    setForm(EMPTY)
    setIntent('')
    setAdding(false)
  }

  /** Hand an intent to the model and get a draft back. It is text in the form, not a saved row:
   *  a procedure nobody has read should not exist, not even as a candidate. */
  const draftIt = async (): Promise<void> => {
    setDrafting(true)
    try {
      const { draft, reason } = await api.skills.draft(intent)
      if (draft) setForm(draft)
      else toast(reason || 'That was too vague to turn into steps.', 'error')
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setDrafting(false)
    }
  }

  return (
    <div className="library-panel">
      <div className="add-row">
        <button className="primary-btn" onClick={() => setAdding(!adding)}><Plus size={14} /> New procedure</button>
        <span className="muted small">
          A procedure is method, not fact: how a task went well, so it can go that way again. Approved ones are injected
          as clearly fenced reference material — they cannot grant the assistant permissions or change its instructions.
        </span>
      </div>
      <InjectedPreview />
      {adding && (
        <div className="skill-body standalone">
          <div className="draft-row">
            <input value={intent} placeholder="Or say what it is for — “file a receipt”, “prep for a 1:1”"
              onChange={(e) => setIntent(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && intent.trim().length > 3) void draftIt() }} />
            <button className="small" disabled={drafting || intent.trim().length < 4} onClick={() => void draftIt()}>
              <Sparkles size={13} /> {drafting ? 'Drafting…' : 'Draft the steps'}
            </button>
          </div>
          <SkillFields value={form} onChange={setForm} />
          <div className="row-actions">
            <button className="primary-btn small" disabled={!form.name.trim()} onClick={() => void add()}>Add as candidate</button>
            <button className="small" onClick={() => { setAdding(false); setForm(EMPTY); setIntent('') }}>Cancel</button>
            <span className="muted small">Saved unapproved, like everything else here — one more click turns it on.</span>
          </div>
        </div>
      )}
      {skills.length === 0 && !adding && (
        <div className="empty-hint big">
          <p>No procedures yet.</p>
          <p className="muted small">Finish something worth repeating in a chat, then use “Learn a procedure” in that chat’s menu — or write one here.</p>
        </div>
      )}
      {ORDER.map((status) => {
        const rows = skills.filter((s) => s.status === status)
        if (!rows.length) return null
        return (
          <section key={status} className="skill-section">
            <h4>{SECTION[status].title} <span className="count">{rows.length}</span></h4>
            <p className="muted small">{SECTION[status].blurb}</p>
            {rows.map((s) => <SkillRow key={s.id} skill={s} />)}
          </section>
        )
      })}
    </div>
  )
}
