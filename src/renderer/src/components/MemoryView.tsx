import { useEffect, useState } from 'react'
import { Plus, Pin, PinOff, Trash2, Wand2, User } from 'lucide-react'
import { useStore, type Scope } from '../store'
import type { Memory } from '@shared/types'
import ProjectChip from './ProjectChip'

const KINDS = ['fact', 'preference', 'goal', 'note']

function MemoryRow({ m, showProject }: { m: Memory; showProject: boolean }): JSX.Element {
  const { updateMemory, deleteMemory } = useStore()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(m.content)
  const commit = (): void => {
    setEditing(false)
    if (draft.trim() && draft !== m.content) void updateMemory(m.id, { content: draft })
    else setDraft(m.content)
  }
  return (
    <div className={`mem-row ${m.pinned ? 'pinned' : ''}`}>
      <div className="mem-main">
        {editing ? (
          <textarea autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={commit}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); commit() } if (e.key === 'Escape') { setDraft(m.content); setEditing(false) } }} />
        ) : (
          <p onClick={() => setEditing(true)} title="Click to edit">{m.content}</p>
        )}
        <div className="mem-meta">
          <select aria-label="Memory kind" value={m.kind} onChange={(e) => void updateMemory(m.id, { kind: e.target.value })}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select>
          <span className="tag" title={m.source === 'auto' ? 'Extracted automatically' : 'Added by you'}>{m.source === 'auto' ? <Wand2 size={10} /> : <User size={10} />}{m.source}</span>
          {showProject && <ProjectChip projectId={m.project_id} showPersonal />}
          {m.project_id && <button className="link small" title="Make this memory available in every chat" onClick={() => void updateMemory(m.id, { move_to_global: true })}>make personal</button>}
          <span className="muted">{new Date(m.updated_at * 1000).toLocaleDateString()}</span>
        </div>
      </div>
      <div className="mem-actions">
        <button className="icon-btn" aria-label={m.pinned ? `Unpin memory: ${m.content.slice(0, 60)}` : `Pin memory (always in context): ${m.content.slice(0, 60)}`} title={m.pinned ? 'Unpin' : 'Pin (always in context)'} onClick={() => void updateMemory(m.id, { pinned: !m.pinned })}>{m.pinned ? <PinOff size={14} /> : <Pin size={14} />}</button>
        <button className="icon-btn danger" aria-label={`Forget memory: ${m.content.slice(0, 60)}`} title="Forget" onClick={() => void deleteMemory(m.id)}><Trash2 size={14} /></button>
      </div>
    </div>
  )
}

/**
 * The memory-list half of the Memory panel. The panel owns the scope filter and
 * the search box and passes the query down; this component never renders a page
 * header of its own.
 */
export default function MemoryView({ projectId, query = '' }: { projectId?: string; query?: string }): JSX.Element {
  const memories = useStore((s) => s.memories)
  const libraryScope = useStore((s) => s.libraryScope)
  const { refreshMemories, addMemory } = useStore()
  const scope: Scope = projectId ?? libraryScope
  const [draft, setDraft] = useState('')
  const [kind, setKind] = useState('fact')

  useEffect(() => { void refreshMemories(query) }, [query, refreshMemories])

  const targetProject = scope === 'all' || scope === 'personal' ? null : scope
  const add = async (): Promise<void> => {
    if (!draft.trim()) return
    await addMemory(draft, kind, targetProject)
    setDraft('')
  }

  return (
    <div className="page-body mem-body">
      <div className="add-row">
        <input placeholder={`Remember something${targetProject ? ' in this project' : ''}…`} value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void add()} />
        <select aria-label="Kind for the new memory" value={kind} onChange={(e) => setKind(e.target.value)}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select>
        <button className="primary-btn" onClick={() => void add()} disabled={!draft.trim()}><Plus size={14} /> Add</button>
      </div>
      <p className="muted small">
        {projectId
          ? 'Project memories are injected into chats in this project, on top of your personal memories.'
          : 'Personal memories go into every chat; project memories only into that project’s chats. Pinned ones are always included; the rest are chosen by recency and relevance. Click a memory to edit it.'}
      </p>
      {memories.length === 0 && <p className="empty-hint big">{query ? 'No memories match.' : 'No memories here yet. Chat with auto-learn on, or add one above.'}</p>}
      {memories.map((m) => <MemoryRow key={m.id} m={m} showProject={scope === 'all'} />)}
    </div>
  )
}
