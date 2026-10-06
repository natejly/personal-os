import { useEffect, useMemo, useState } from 'react'
import { Check, ChevronDown, ChevronRight, Download, Eye, Globe, Plus, Sparkles, Trash2, Undo2, Upload, Video, X } from 'lucide-react'
import { useStore } from '../store'
import type { Skill, SkillFinding, SkillPreview } from '@shared/types'
import { PRESET_KIND_LABEL, SKILL_PRESETS, type SkillPreset } from '@shared/skillPresets'
import ProjectChip from './ProjectChip'
import TeachTaskPanel from './TeachTaskPanel'
import { api } from '../lib/api'
import { rowButton } from '../lib/rowButton'
import { debounceLatest, LINT_DELAY_MS, skillDisclosure } from '../lib/skillLint'
import { skillSlug } from '../lib/slashCommands'

type SkillText = { name: string; description: string; procedure: string }

/** The backend lint for the text being edited, re-run as typing pauses. Null until the first reply. */
function useSkillLint(d: SkillText, enabled: boolean, skillId?: string): SkillFinding[] | null {
  const [findings, setFindings] = useState<SkillFinding[] | null>(null)
  const lint = useMemo(() => debounceLatest((x: SkillText & { skill_id?: string }) => api.skills.lint(x), LINT_DELAY_MS,
    (r) => setFindings(r ? r.findings : null)), [])
  useEffect(() => lint.cancel, [lint])
  useEffect(() => {
    if (enabled) lint.call({ ...d, skill_id: skillId })
    else lint.cancel()
  }, [enabled, d.name, d.description, d.procedure, skillId, lint])
  return enabled ? findings : null
}

function Findings({ findings }: { findings: SkillFinding[] | null }): JSX.Element | null {
  if (!findings?.length) return null
  return (
    <ul className="skill-findings" aria-live="polite">
      {findings.map((f, i) => (
        <li key={i} className={f.level}>
          <strong>{f.level === 'error' ? 'Blocks approval' : 'Check'}</strong> {f.message}
          {f.hint && <span className="muted"> {f.hint}</span>}
        </li>
      ))}
    </ul>
  )
}

/** Why a skill exists, in the user's terms. 'proposed' is reserved for the assistant asking directly. */
const SOURCE_LABEL: Record<Skill['source'], string> = {
  induced: 'learned from a chat',
  proposed: 'suggested by the assistant',
  user: 'written by you',
  teach: 'taught from a screen recording',
  builtin: 'built into Grain'
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
  const [draft, setDraft] = useState<SkillText | null>(null)

  const edit = draft ?? { name: skill.name, description: skill.description, procedure: skill.procedure }
  const findings = useSkillLint(edit, open, skill.id)
  // The same check the approve PATCH refuses on (approval_blockers), shown before the click.
  const blocked = !!findings?.some((f) => f.level === 'error')
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
        <small className="muted">{SOURCE_LABEL[skill.source] ?? skill.source}</small>
        {!!skill.use_count && <small className="muted" title="Times the assistant read this skill">used {skill.use_count}×</small>}
        <div className="skill-actions no-drag" onClick={(e) => e.stopPropagation()}>
          {skill.status !== 'approved' && (
            <button className="primary-btn sm" disabled={blocked}
              title={blocked ? 'Fix what blocks approval first' : 'Let the assistant use this skill'}
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
          {skill.source === 'builtin'
            ? <small className="muted" title="Part of Grain: revoke it to stop using it, it cannot be deleted">built in</small>
            : <ConfirmDelete label={skill.name} onDelete={() => void deleteSkill(skill.id)} />}
        </div>
      </div>
      {open && (
        <div className="skill-body">
          {!!skill.rationale && <p className="muted small">Suggested because: {skill.rationale}</p>}
          <label>Name<input value={edit.name} onChange={(e) => setDraft({ ...edit, name: e.target.value })} /></label>
          <label>When it applies<input value={edit.description} onChange={(e) => setDraft({ ...edit, description: e.target.value })} /></label>
          <label>Steps
            <textarea rows={10} value={edit.procedure} onChange={(e) => setDraft({ ...edit, procedure: e.target.value })} />
          </label>
          <Findings findings={findings} />
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
  const [mdUrl, setMdUrl] = useState('')
  const [browsing, setBrowsing] = useState(false)
  const [teaching, setTeaching] = useState(false)
  const [fetching, setFetching] = useState<string | null>(null) // the preset (or URL) being imported
  const [form, setForm] = useState({ name: '', description: '', procedure: '' })
  const formFindings = useSkillLint(form, adding)
  const [intent, setIntent] = useState('')
  const [drafting, setDrafting] = useState(false)
  const budget = useStore((s) => s.settings.skillsInlineBudget ?? 6000)
  const [preview, setPreview] = useState<SkillPreview | null>(null)

  const draftFromIntent = async (): Promise<void> => {
    setDrafting(true)
    try {
      const r = await api.skills.draft(intent)
      if (r.draft) setForm({ name: r.draft.name, description: r.draft.description, procedure: r.draft.procedure })
      else toast(r.reason || 'No draft came back.', 'error')
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setDrafting(false)
    }
  }

  const togglePreview = async (): Promise<void> => {
    if (preview) return setPreview(null)
    try {
      setPreview(await api.skills.preview())
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const [saving, setSaving] = useState(false)
  const add = async (): Promise<void> => {
    if (!form.name.trim() || saving) return
    setSaving(true)
    try {
      await createSkill(form)
    } finally {
      setSaving(false)
    }
    setForm({ name: '', description: '', procedure: '' })
    setAdding(false)
  }

  /** Pasted text or a URL; a popular skill is a URL import too. Everything lands as a candidate. */
  const importFrom = async (src: { text?: string; url?: string }, label: string): Promise<boolean> => {
    setFetching(label)
    try {
      const r = await api.skills.importMd(src)
      await refreshSkills()
      const notes = [...r.warnings, ...r.findings.map((f) => f.message)]
      toast(`Imported “${r.skill.name}” as a candidate.${notes.length ? ' ' + notes.join(' ') : ''}`, notes.length ? 'error' : undefined)
      return true
    } catch (e) {
      toast((e as Error).message, 'error')
      return false
    } finally {
      setFetching(null)
    }
  }

  const importMd = async (): Promise<void> => {
    const url = mdUrl.trim()
    if (await importFrom(url ? { url } : { text: mdText }, url || 'text')) {
      setMdText('')
      setMdUrl('')
      setImporting(false)
    }
  }

  const importPreset = (p: SkillPreset): Promise<boolean> => importFrom({ url: p.url }, p.name)

  const empty = skills.length === 0 && !adding && !importing && !browsing && !teaching

  return (
    <div className="library-panel">
      {/* One toolbar row, the buttons the same height, with the explanation as a line under it.
          With nothing listed the empty state below offers the same actions instead. */}
      {!empty && (
        <div className="library-toolbar">
          <div className="library-toolbar-row">
            <button className="primary-btn" aria-expanded={adding} onClick={() => setAdding(!adding)}><Plus size={14} /> New skill</button>
            <button className={`ghost-btn${importing ? ' on' : ''}`} aria-expanded={importing} onClick={() => setImporting(!importing)}><Upload size={14} /> Import skill file</button>
            <button className={`ghost-btn${browsing ? ' on' : ''}`} aria-expanded={browsing} onClick={() => setBrowsing(!browsing)}><Globe size={14} /> Popular skills</button>
            <button className={`ghost-btn${teaching ? ' on' : ''}`} aria-expanded={teaching} onClick={() => setTeaching(!teaching)}><Video size={14} /> Teach a task</button>
            <button className={`ghost-btn${preview ? ' on' : ''}`} aria-pressed={!!preview} onClick={() => void togglePreview()}><Eye size={14} /> What the assistant sees</button>
          </div>
          <p className="muted small">A skill is a way of doing a task. It stays off until you approve it. In a chat, type <code>/skill</code> to use one on purpose.</p>
        </div>
      )}
      {teaching && <TeachTaskPanel onClose={() => setTeaching(false)} />}
      {browsing && (
        <div className="skill-body standalone">
          <p className="muted small">
            Skills the open-source community publishes in the SKILL.md format. Import fetches the file from its repository and
            adds it here as a candidate for you to read and approve; bundled scripts and assets are not imported, so a skill
            that calls them may need editing. Any other skill: Import skill file takes a link to a SKILL.md, its folder, or a page for either.
          </p>
          {(Object.keys(PRESET_KIND_LABEL) as SkillPreset['kind'][]).map((kind) => (
            <section key={kind} className="preset-group">
              <h5>{PRESET_KIND_LABEL[kind]}</h5>
              {SKILL_PRESETS.filter((p) => p.kind === kind).map((p) => {
                const have = skills.some((s) => skillSlug(s.name) === p.name)
                return (
                  <div key={p.name} className="preset-row">
                    <div className="preset-text">
                      <strong>{p.name}</strong> <span className="muted">{p.blurb}</span>
                      <small className="muted">{p.source} · {p.license} · {p.chars < 1000 ? `${p.chars} characters` : `${Math.round(p.chars / 1000)}k characters`}</small>
                    </div>
                    <button className="ghost-btn sm" disabled={have || fetching === p.name} onClick={() => void importPreset(p)}
                      title={have ? 'Already in your skills' : `Fetch from ${p.url}`}>
                      {have ? <><Check size={13} /> Imported</> : fetching === p.name ? 'Importing…' : <><Download size={13} /> Import</>}
                    </button>
                  </div>
                )
              })}
            </section>
          ))}
        </div>
      )}
      {preview && (
        <div className="skill-body standalone">
          {preview.block ? (
            <p className="muted small">
              {preview.included.length} approved skill{preview.included.length === 1 ? '' : 's'}, about {preview.tokens_estimate} tokens.{' '}
              {skillDisclosure(preview.block, budget) === 'inline'
                ? `Under the ${budget}-character budget, so each chat gets the full text below.`
                : `Over the ${budget}-character budget, so chats get only names and one-line descriptions and open a procedure when it fits. Below is the full text.`}
              {preview.omitted.length > 0 && ` Past the procedure limit and left out: ${preview.omitted.map((o) => o.name).join(', ')}.`}
            </p>
          ) : <p className="muted small">No approved procedures, so chats see nothing from here.</p>}
          {preview.block && <pre className="skill-preview">{preview.block}</pre>}
        </div>
      )}
      {importing && (
        <div className="skill-body standalone">
          <label>Link to a SKILL.md, its folder, or a repository page for either
            <input autoFocus value={mdUrl} placeholder="https://github.com/owner/repo/tree/main/skills/weekly-review"
              onChange={(e) => setMdUrl(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && mdUrl.trim() && !fetching) void importMd() }} />
          </label>
          <label>…or paste a SKILL.md
            <textarea rows={8} value={mdText} disabled={!!mdUrl.trim()} placeholder={'---\nname: weekly-review\ndescription: Use when the user asks for a weekly review\n---\n\n1. Pull the done todos.'} onChange={(e) => setMdText(e.target.value)} />
          </label>
          <div className="row-actions">
            <button className="primary-btn sm" disabled={!(mdUrl.trim() || mdText.trim()) || !!fetching} onClick={() => void importMd()}>{fetching ? 'Importing…' : 'Import as candidate'}</button>
            <button className="ghost-btn sm" onClick={() => setImporting(false)}>Cancel</button>
            <span className="muted small">Imported skills wait for your approval; allowed-tools and bundled files are ignored.</span>
          </div>
        </div>
      )}
      {adding && (
        <div className="skill-body standalone">
          <div className="skill-intent">
            <input value={intent} placeholder="Draft from a sentence: “when I ask for a weekly review, …”"
              onChange={(e) => setIntent(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && intent.trim() && !drafting) void draftFromIntent() }} />
            <button className="ghost-btn sm" disabled={!intent.trim() || drafting} onClick={() => void draftFromIntent()}>
              <Sparkles size={13} /> {drafting ? 'Drafting…' : 'Draft'}</button>
          </div>
          <label>Name<input autoFocus value={form.name} placeholder="Weekly review" onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label>When it applies<input value={form.description} placeholder="when I ask for a weekly review" onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
          <label>Steps<textarea rows={8} value={form.procedure} placeholder={'1. Pull this week\'s done todos.\n2. Check the calendar for what slipped.\n3. Draft the summary as bullets.'} onChange={(e) => setForm({ ...form, procedure: e.target.value })} /></label>
          {(form.name || form.procedure) && <Findings findings={formFindings} />}
          <div className="row-actions">
            <button className="primary-btn sm" disabled={!form.name.trim() || saving} onClick={() => void add()}>Add as candidate</button>
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
            <button className="ghost-btn" onClick={() => setImporting(true)}><Upload size={14} /> Import skill file</button>
            <button className="ghost-btn" onClick={() => setBrowsing(true)}><Globe size={14} /> Popular skills</button>
            <button className="ghost-btn" onClick={() => setTeaching(true)}><Video size={14} /> Teach a task</button>
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
