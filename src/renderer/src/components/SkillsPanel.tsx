import { useEffect, useState } from 'react'
import { Check, ChevronDown, ChevronRight, Download, Plus, Sparkles, Trash2, Undo2, Upload, X } from 'lucide-react'
import { useStore } from '../store'
import type { Skill } from '@shared/types'
import ProjectChip from './ProjectChip'
import { api } from '../lib/api'
import { rowButton } from '../lib/rowButton'

/** Why a skill exists, in the user's terms. 'proposed' is reserved for the assistant asking directly. */
const SOURCE_LABEL: Record<Skill['source'], string> = {
  induced: 'learned from a chat',
  proposed: 'suggested by the assistant',
  user: 'written by you'
}

const ORDER: Skill['status'][] = ['candidate', 'approved', 'rejected']
const SECTION: Record<Skill['status'], { title: string; blurb: string }> = {
  candidate: { title: 'Waiting for you', blurb: 'Nothing here is in use. Read it, edit it if you like, then approve or discard.' },
  approved: { title: 'In use', blurb: 'Injected into chats that have skills turned on, as reference material the assistant may follow.' },
  rejected: { title: 'Discarded', blurb: 'Kept so the same suggestion is recognisable if it comes back.' }
}

/**
 * Delete in two steps, in place. A skill or a workflow is gone for good (nothing lands in Trash), so
 * the first click only arms the button: it becomes "Delete?" beside "Keep", and stands down on its own
 * after a few seconds or when focus leaves. "Keep" takes the spot the bin was in and the focus, so a
 * double click or a second Enter lands on the safe answer.
 */
export function ConfirmDelete({ label, onDelete }: { label: string; onDelete: () => void }): JSX.Element {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const t = setTimeout(() => setArmed(false), 5000)
    return () => clearTimeout(t)
  }, [armed])

  if (!armed) {
    return <button className="icon-btn ghost danger" aria-label={`Delete ${label}`} title="Delete" onClick={() => setArmed(true)}><Trash2 size={13} /></button>
  }
  return (
    <span className="confirm-del" onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setArmed(false) }}>
      <button className="primary-btn sm danger" aria-label={`Delete ${label} for good`} onClick={() => { setArmed(false); onDelete() }}>Delete?</button>
      <button className="ghost-btn sm" autoFocus onClick={() => setArmed(false)}>Keep</button>
    </span>
  )
}

function SkillRow({ skill }: { skill: Skill }): JSX.Element {
  const { updateSkill, deleteSkill, toast } = useStore()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<{ name: string; description: string; procedure: string } | null>(null)

  const edit = draft ?? { name: skill.name, description: skill.description, procedure: skill.procedure }
  const dirty = draft !== null && (draft.name !== skill.name || draft.description !== skill.description || draft.procedure !== skill.procedure)
  const save = async (): Promise<void> => {
    if (dirty) await updateSkill(skill.id, draft!)
    setDraft(null)
  }

  const exportSkill = async (): Promise<void> => {
    try {
      const { filename, text } = await api.skills.exportMd(skill.id)
      const a = document.createElement('a')
      a.href = URL.createObjectURL(new Blob([text], { type: 'text/markdown' }))
      a.download = filename.replace('/', '-')
      a.click()
      URL.revokeObjectURL(a.href)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  return (
    <div className={`skill-row ${skill.status}`}>
      <div className="skill-head" aria-expanded={open} {...rowButton(() => setOpen(!open))}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="skill-name">{skill.name}</span>
        <span className="skill-desc muted">{skill.description}</span>
        <ProjectChip projectId={skill.project_id} showPersonal />
        <small className="muted">{SOURCE_LABEL[skill.source]}</small>
        <div className="skill-actions no-drag" onClick={(e) => e.stopPropagation()}>
          {skill.status !== 'approved' && (
            <button className="primary-btn sm" title="Let the assistant use this skill"
              onClick={() => void updateSkill(skill.id, { status: 'approved' })}><Check size={13} /> Approve</button>
          )}
          {skill.status === 'approved' && (
            <button className="ghost-btn sm" title="Stop injecting this skill"
              onClick={() => void updateSkill(skill.id, { status: 'candidate' })}><Undo2 size={13} /> Revoke</button>
          )}
          {skill.status === 'candidate' && (
            <button className="ghost-btn sm" title="Discard: it stays listed under Discarded" onClick={() => void updateSkill(skill.id, { status: 'rejected' })}><X size={13} /> Discard</button>
          )}
          <button className="icon-btn ghost" aria-label={`Export ${skill.name} as SKILL.md`} title="Export as SKILL.md"
            onClick={() => void exportSkill()}><Download size={13} /></button>
          <ConfirmDelete label={skill.name} onDelete={() => void deleteSkill(skill.id)} />
        </div>
      </div>
      {open && (
        <div className="skill-body">
          <label>Name<input value={edit.name} onChange={(e) => setDraft({ ...edit, name: e.target.value })} /></label>
          <label>When it applies<input value={edit.description} onChange={(e) => setDraft({ ...edit, description: e.target.value })} /></label>
          <label>Steps
            <textarea rows={10} value={edit.procedure} onChange={(e) => setDraft({ ...edit, procedure: e.target.value })} />
          </label>
          <div className="row-actions">
            <button className="primary-btn sm" disabled={!dirty} onClick={() => void save()}>Save</button>
            {dirty && <button className="ghost-btn sm" onClick={() => setDraft(null)}>Cancel</button>}
            {skill.status === 'approved' && <span className="muted small">Edits take effect in the next reply.</span>}
          </div>
        </div>
      )}
    </div>
  )
}

export default function SkillsPanel(): JSX.Element {
  const skills = useStore((s) => s.skills)
  const { createSkill, refreshSkills, toast } = useStore()
  const [adding, setAdding] = useState(false)
  const [importing, setImporting] = useState(false)
  const [mdText, setMdText] = useState('')
  const [form, setForm] = useState({ name: '', description: '', procedure: '' })

  const add = async (): Promise<void> => {
    if (!form.name.trim()) return
    await createSkill(form)
    setForm({ name: '', description: '', procedure: '' })
    setAdding(false)
  }

  const importMd = async (): Promise<void> => {
    try {
      const r = await api.skills.importMd(mdText)
      await refreshSkills()
      const notes = [...r.warnings, ...r.findings.map((f) => f.message)]
      toast(`Imported “${r.skill.name}” as a candidate.${notes.length ? ' ' + notes.join(' ') : ''}`, notes.length ? 'error' : undefined)
      setMdText('')
      setImporting(false)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const empty = skills.length === 0 && !adding && !importing

  return (
    <div className="library-panel">
      {/* One toolbar row, both buttons the same height, with the explanation as a line under it.
          With nothing listed the empty state below offers the same two actions instead. */}
      {!empty && (
        <div className="library-toolbar">
          <div className="library-toolbar-row">
            <button className="primary-btn" aria-expanded={adding} onClick={() => setAdding(!adding)}><Plus size={14} /> New skill</button>
            <button className={`ghost-btn${importing ? ' on' : ''}`} aria-expanded={importing} onClick={() => setImporting(!importing)}><Upload size={14} /> Import SKILL.md</button>
          </div>
          <p className="muted small">A skill is a way of doing a task. It stays off until you approve it.</p>
        </div>
      )}
      {importing && (
        <div className="skill-body standalone">
          <label>Paste a SKILL.md
            <textarea rows={10} autoFocus value={mdText} placeholder={'---\nname: weekly-review\ndescription: Use when the user asks for a weekly review\n---\n\n1. Pull the done todos.'} onChange={(e) => setMdText(e.target.value)} />
          </label>
          <div className="row-actions">
            <button className="primary-btn sm" disabled={!mdText.trim()} onClick={() => void importMd()}>Import as candidate</button>
            <button className="ghost-btn sm" onClick={() => setImporting(false)}>Cancel</button>
            <span className="muted small">Imported skills wait for your approval; allowed-tools and bundled files are ignored.</span>
          </div>
        </div>
      )}
      {adding && (
        <div className="skill-body standalone">
          <label>Name<input autoFocus value={form.name} placeholder="Weekly review" onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label>When it applies<input value={form.description} placeholder="when I ask for a weekly review" onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
          <label>Steps<textarea rows={8} value={form.procedure} placeholder={'1. Pull this week\'s done todos.\n2. Check the calendar for what slipped.\n3. Draft the summary as bullets.'} onChange={(e) => setForm({ ...form, procedure: e.target.value })} /></label>
          <div className="row-actions">
            <button className="primary-btn sm" disabled={!form.name.trim()} onClick={() => void add()}>Add as candidate</button>
            <button className="ghost-btn sm" onClick={() => setAdding(false)}>Cancel</button>
            <span className="muted small">It stays off until you approve it.</span>
          </div>
        </div>
      )}
      {empty && (
        <div className="empty-state">
          <Sparkles size={28} />
          <h2>No skills yet</h2>
          <p>A skill is a way of doing a task that the assistant can follow again. Write one, or save a reply that used tools. It stays off until you approve it.</p>
          <div className="library-toolbar-row">
            <button className="primary-btn" onClick={() => setAdding(true)}><Plus size={14} /> New skill</button>
            <button className="ghost-btn" onClick={() => setImporting(true)}><Upload size={14} /> Import SKILL.md</button>
          </div>
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
