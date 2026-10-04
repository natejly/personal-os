import { useState } from 'react'
import { X, Trash2 } from 'lucide-react'
import { useStore } from '../store'
import { useModal } from '../lib/useModal'
import { ToolOverrides } from './ToolPermissions'
import type { ToolOverride } from '@shared/types'

const COLORS = ['#d97757', '#e5484d', '#e5a13b', '#46a758', '#3b9edb', '#8e6fdb', '#d95c9e', '#8b8b8b']
const COLOR_NAMES = ['Terracotta', 'Red', 'Amber', 'Green', 'Blue', 'Purple', 'Pink', 'Gray']

export default function ProjectModal(): JSX.Element {
  const modal = useStore((s) => s.projectModal)!
  const { setProjectModal, createProject, updateProject, deleteProject } = useStore()
  const existing = modal.mode === 'edit' ? modal.project : null
  const [name, setName] = useState(existing?.name ?? '')
  const [description, setDescription] = useState(existing?.description ?? '')
  const [color, setColor] = useState(existing?.color ?? COLORS[0])
  const [tools, setTools] = useState<Record<string, ToolOverride>>(existing?.tools ?? {})
  const globalToolsRaw = useStore((s) => s.settings.tools)
  const allTools = useStore((s) => s.tools)
  const globalTools = Object.fromEntries(allTools.map((t) => { const v = globalToolsRaw?.[t.name]; return [t.name, v === true ? 'on' : v === false ? 'off' : v === 'on' || v === 'ask' || v === 'off' ? v : t.default_mode] }))
  const [confirmDelete, setConfirmDelete] = useState(false)
  const { titleId, backdrop, modal: dialog } = useModal(() => setProjectModal(null))

  const save = async (): Promise<void> => {
    if (!name.trim()) return
    if (existing) await updateProject(existing.id, { name: name.trim(), description, color, tools })
    else {
      await createProject({ name: name.trim(), description, system_prompt: '', color })
      const created = useStore.getState().projects.find((p) => p.name === name.trim())
      if (created && Object.keys(tools).length) await updateProject(created.id, { tools })
    }
    setProjectModal(null)
  }

  return (
    <div className="modal-backdrop" {...backdrop}>
      <div className="modal" {...dialog}>
        <header><h2 id={titleId}>{existing ? 'Edit project' : 'New project'}</h2><button className="icon-btn" aria-label={existing ? 'Close edit project' : 'Close new project'} title="Close" onClick={() => setProjectModal(null)}><X size={16} /></button></header>
        <section>
          <label><span>Name</span><input autoFocus value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void save()} /></label>
          <label><span>Description</span><input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Shown to the model" /></label>
          <label><span>Color</span>
            <div className="color-row">{COLORS.map((c, i) => <button key={c} type="button" className={`swatch ${c === color ? 'on' : ''}`} style={{ background: c }} aria-label={COLOR_NAMES[i]} aria-pressed={c === color} title={COLOR_NAMES[i]} onClick={() => setColor(c)} />)}</div>
          </label>
        </section>
        <section>
          <h3>Tools</h3>
          <p className="muted">"Inherit" follows your global settings.</p>
          <ToolOverrides value={tools} onChange={setTools} effectiveBase={globalTools} compact />
        </section>
        <footer>
          {existing && (
            confirmDelete
              ? <button className="ghost-btn danger" onClick={() => { void deleteProject(existing.id); setProjectModal(null) }}><Trash2 size={14} /> Really delete this project and its chats?</button>
              : <button className="ghost-btn danger" onClick={() => setConfirmDelete(true)}><Trash2 size={14} /> Delete project</button>
          )}
          <span style={{ flex: 1 }} />
          <button className="ghost-btn" onClick={() => setProjectModal(null)}>Cancel</button>
          <button className="primary-btn" onClick={() => void save()} disabled={!name.trim()}>{existing ? 'Save' : 'Create'}</button>
        </footer>
      </div>
    </div>
  )
}
