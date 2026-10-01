import { useEffect, useState } from 'react'
import { Check, ChevronDown, ChevronRight, Trash2, Undo2, X } from 'lucide-react'
import { useStore } from '../store'
import type { Skill } from '@shared/types'

const SOURCE: Record<string, string> = {
  induced: 'learned from a chat',
  proposed: 'proposed by the assistant',
  user: 'written by you'
}

function SkillRow({ skill }: { skill: Skill }): JSX.Element {
  const { updateSkill, deleteSkill } = useStore()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState({ name: skill.name, description: skill.description, procedure: skill.procedure })
  const dirty = draft.name !== skill.name || draft.description !== skill.description || draft.procedure !== skill.procedure

  return (
    <li className={`skill-row ${skill.status}`}>
      <div className="skill-head">
        <button className="skill-title" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          <b>{skill.name}</b>
          <span className={`skill-badge ${skill.status}`}>{skill.status}</span>
          <small className="muted">{SOURCE[skill.source] ?? skill.source}</small>
        </button>
        <div className="skill-actions">
          {skill.status !== 'approved' && (
            <button className="ghost-btn" title="Approve: this procedure starts being injected into new replies"
              onClick={() => void updateSkill(skill.id, { status: 'approved' })}><Check size={13} /> Approve</button>
          )}
          {skill.status === 'candidate' && (
            <button className="ghost-btn" title="Reject: keep it listed but never inject it"
              onClick={() => void updateSkill(skill.id, { status: 'rejected' })}><X size={13} /> Reject</button>
          )}
          {skill.status === 'approved' && (
            <button className="ghost-btn" title="Stop injecting this procedure"
              onClick={() => void updateSkill(skill.id, { status: 'candidate' })}><Undo2 size={13} /> Revoke</button>
          )}
          <button className="icon-btn" aria-label="Delete skill" title="Delete" onClick={() => void deleteSkill(skill.id)}><Trash2 size={14} /></button>
        </div>
      </div>
      {!open && skill.description && <p className="muted small skill-desc">{skill.description}</p>}
      {open && (
        <div className="skill-edit">
          <label><span>Name</span><input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
          <label><span>When it applies</span><input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></label>
          <label><span>Procedure</span><textarea rows={8} value={draft.procedure} onChange={(e) => setDraft({ ...draft, procedure: e.target.value })} /></label>
          <div className="skill-edit-actions">
            <button className="ghost-btn" disabled={!dirty} onClick={() => void updateSkill(skill.id, draft)}>Save changes</button>
            {dirty && <button className="link" onClick={() => setDraft({ name: skill.name, description: skill.description, procedure: skill.procedure })}>revert</button>}
          </div>
        </div>
      )}
    </li>
  )
}

/**
 * Review surface for procedural memory. The rule this UI exists to enforce: a skill the model wrote
 * is a *candidate* — inert text in a table — until someone reads it here and approves it. Only then
 * does it get injected, and even then it goes in fenced and labelled as approved procedural memory.
 */
export default function SkillsReview(): JSX.Element {
  const skills = useStore((s) => s.skills)
  const { refreshSkills } = useStore()
  const [showRejected, setShowRejected] = useState(false)

  useEffect(() => { void refreshSkills() }, [refreshSkills])

  const candidates = skills.filter((s) => s.status === 'candidate')
  const approved = skills.filter((s) => s.status === 'approved')
  const rejected = skills.filter((s) => s.status === 'rejected')

  return (
    <div className="skills-review">
      <p className="muted">
        A skill is a procedure the assistant can follow again. Anything it writes arrives as a <b>candidate</b> and does
        nothing: read it, rename it, edit it, then approve it. Only approved skills are added to the system prompt, and
        they go in clearly marked as your approved procedural memory.
      </p>
      {!skills.length && <p className="muted small">No skills yet. Ask the assistant to remember how a task is done, or use “propose a skill from this chat” in the context panel.</p>}
      {candidates.length > 0 && (
        <>
          <h4>Waiting for review ({candidates.length})</h4>
          <ul className="skill-list">{candidates.map((s) => <SkillRow key={s.id} skill={s} />)}</ul>
        </>
      )}
      {approved.length > 0 && (
        <>
          <h4>Approved · injected into new replies ({approved.length})</h4>
          <ul className="skill-list">{approved.map((s) => <SkillRow key={s.id} skill={s} />)}</ul>
        </>
      )}
      {rejected.length > 0 && (
        <>
          <h4><button className="link" onClick={() => setShowRejected((v) => !v)}>{showRejected ? 'hide' : 'show'} rejected ({rejected.length})</button></h4>
          {showRejected && <ul className="skill-list">{rejected.map((s) => <SkillRow key={s.id} skill={s} />)}</ul>}
        </>
      )}
    </div>
  )
}
