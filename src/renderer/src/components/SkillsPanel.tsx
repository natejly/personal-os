import { useState } from 'react'
import { Check, ChevronDown, ChevronRight, Download, Plus, Trash2, Undo2, Upload, X } from 'lucide-react'
import { useStore } from '../store'
import type { Skill } from '@shared/types'
import ProjectChip from './ProjectChip'
import { api } from '../lib/api'

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
      <div className="skill-head" onClick={() => setOpen(!open)} role="button" tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter') setOpen(!open) }}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="skill-name">{skill.name}</span>
        <span className="skill-desc muted">{skill.description}</span>
        <ProjectChip projectId={skill.project_id} showPersonal />
        <small className="muted">{SOURCE_LABEL[skill.source]}</small>
        <div className="skill-actions no-drag" onClick={(e) => e.stopPropagation()}>
          {skill.status !== 'approved' && (
            <button className="primary-btn small" title="Let the assistant use this procedure"
              onClick={() => void updateSkill(skill.id, { status: 'approved' })}><Check size={13} /> Approve</button>
          )}
          {skill.status === 'approved' && (
            <button className="small" title="Stop injecting this procedure"
              onClick={() => void updateSkill(skill.id, { status: 'candidate' })}><Undo2 size={13} /> Revoke</button>
          )}
          {skill.status === 'candidate' && (
            <button className="small" title="Discard" onClick={() => void updateSkill(skill.id, { status: 'rejected' })}><X size={13} /></button>
          )}
          <button className="icon-btn ghost" aria-label={`Export ${skill.name} as SKILL.md`} title="Export as SKILL.md"
            onClick={() => void exportSkill()}><Download size={13} /></button>
          <button className="icon-btn ghost danger" aria-label={`Delete ${skill.name}`} onClick={() => void deleteSkill(skill.id)}><Trash2 size={13} /></button>
        </div>
      </div>
      {open && (
        <div className="skill-body">
          <label>Name<input value={edit.name} onChange={(e) => setDraft({ ...edit, name: e.target.value })} /></label>
          <label>When it applies<input value={edit.description} onChange={(e) => setDraft({ ...edit, description: e.target.value })} /></label>
          <label>Procedure
            <textarea rows={10} value={edit.procedure} onChange={(e) => setDraft({ ...edit, procedure: e.target.value })} />
          </label>
          <div className="row-actions">
            <button className="primary-btn small" disabled={!dirty} onClick={() => void save()}>Save</button>
            {dirty && <button className="small" onClick={() => setDraft(null)}>Cancel</button>}
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

  return (
    <div className="library-panel">
      <div className="add-row">
        <button className="primary-btn" onClick={() => setAdding(!adding)}><Plus size={14} /> New procedure</button>
        <button onClick={() => setImporting(!importing)}><Upload size={14} /> Import SKILL.md</button>
        <span className="muted small">A way of doing a task. It stays off until you approve it.</span>
      </div>
      {importing && (
        <div className="skill-body standalone">
          <label>Paste a SKILL.md
            <textarea rows={10} autoFocus value={mdText} placeholder={'---\nname: weekly-review\ndescription: Use when the user asks for a weekly review\n---\n\n1. Pull the done todos.'} onChange={(e) => setMdText(e.target.value)} />
          </label>
          <div className="row-actions">
            <button className="primary-btn small" disabled={!mdText.trim()} onClick={() => void importMd()}>Import as candidate</button>
            <button className="small" onClick={() => setImporting(false)}>Cancel</button>
            <span className="muted small">Imported procedures wait for your approval; allowed-tools and bundled files are ignored.</span>
          </div>
        </div>
      )}
      {adding && (
        <div className="skill-body standalone">
          <label>Name<input autoFocus value={form.name} placeholder="Weekly review" onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label>When it applies<input value={form.description} placeholder="when I ask for a weekly review" onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
          <label>Procedure<textarea rows={8} value={form.procedure} placeholder={'1. Pull this week\'s done todos.\n2. Check the calendar for what slipped.\n3. Draft the summary as bullets.'} onChange={(e) => setForm({ ...form, procedure: e.target.value })} /></label>
          <div className="row-actions">
            <button className="primary-btn small" disabled={!form.name.trim()} onClick={() => void add()}>Add as candidate</button>
            <button className="small" onClick={() => setAdding(false)}>Cancel</button>
            <span className="muted small">It stays off until you approve it.</span>
          </div>
        </div>
      )}
      {skills.length === 0 && !adding && (
        <div className="empty-hint big">
          <p>No procedures yet.</p>
          <p className="muted small">Write one here, or save a reply that used tools.</p>
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
