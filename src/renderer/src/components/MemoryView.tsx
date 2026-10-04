import { useEffect, useState } from 'react'
import { Plus, Pin, PinOff, Trash2, Wand2, User, History, Undo2, Sparkles, Check, X } from 'lucide-react'
import { useStore, type Scope } from '../store'
import type { Memory, MemoryProposal } from '@shared/types'
import ProjectChip from './ProjectChip'
import { api } from '../lib/api'

const KINDS = ['fact', 'preference', 'goal', 'note']

function MemoryRow({ m, showProject }: { m: Memory; showProject: boolean }): JSX.Element {
  const { updateMemory, deleteMemory, selectChat, projects } = useStore()
  const isolated = projects.some((p) => p.id === m.project_id && p.memory_mode === 'isolated')
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
          {m.project_id && !isolated && <button className="link small" title="Make this memory available in every chat" onClick={() => void updateMemory(m.id, { move_to_global: true })}>make personal</button>}
          {m.source_conversation_id && <button className="link small" title="Open the chat this was learned from" onClick={() => void selectChat(m.source_conversation_id as string)}>from chat</button>}
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

/** A pending tidy-up: before/after, applied only when the user says so. */
function ProposalRow({ p, byId, labels, onApply, onDismiss }: { p: MemoryProposal; byId: Map<string, Memory>; labels: Map<string, string>; onApply: () => void; onDismiss: () => void }): JSX.Element {
  const before = p.kind === 'merge_entities'
    ? p.payload.ids.map((i) => labels.get(i) ?? p.payload.snapshot[i])
    : p.payload.ids.map((i) => byId.get(i)?.content ?? p.payload.snapshot[i])
  const after = p.kind === 'merge_entities' ? p.payload.label : p.payload.text
  const title = p.kind === 'merge_memories' ? 'Merge duplicates' : p.kind === 'rewrite_memory' ? 'Make the date absolute' : 'Merge entities'
  return (
    <div className="mem-row proposal">
      <div className="mem-main">
        <div className="mem-meta"><span className="tag"><Sparkles size={10} />{title}</span>{p.rationale && <span className="muted">{p.rationale}</span>}</div>
        {before.map((t, i) => <p key={i} style={{ textDecoration: 'line-through', opacity: 0.65 }}>{t}</p>)}
        <p>{after}</p>
      </div>
      <div className="mem-actions">
        <button className="icon-btn" aria-label={`Apply: ${title}`} title="Apply" onClick={onApply}><Check size={14} /></button>
        <button className="icon-btn" aria-label={`Dismiss: ${title}`} title="Dismiss" onClick={onDismiss}><X size={14} /></button>
      </div>
    </div>
  )
}

/** A superseded or forgotten memory: struck through, with what replaced it and a way back. */
function HistoryRow({ m, byId, onRestore }: { m: Memory; byId: Map<string, Memory>; onRestore: () => void }): JSX.Element {
  const next = m.superseded_by ? byId.get(m.superseded_by) : undefined
  return (
    <div className="mem-row history">
      <div className="mem-main">
        <p style={{ textDecoration: 'line-through', opacity: 0.65 }}>{m.content}</p>
        <div className="mem-meta">
          <span className="muted">{next ? `replaced by “${next.content}”` : 'forgotten'}</span>
          <span className="muted">{m.invalid_at ? new Date(m.invalid_at * 1000).toLocaleDateString() : ''}</span>
        </div>
      </div>
      <div className="mem-actions">
        <button className="icon-btn" aria-label={`Restore memory: ${m.content.slice(0, 60)}`} title="Restore" onClick={onRestore}><Undo2 size={14} /></button>
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
  const [showHistory, setShowHistory] = useState(false)
  const [all, setAll] = useState<Memory[]>([])

  useEffect(() => { void refreshMemories(query) }, [query, refreshMemories])
  const loadHistory = (): void => { void api.memories.listWithHistory(scope).then(setAll).catch(() => setAll([])) }
  useEffect(() => { if (showHistory) loadHistory() }, [showHistory, scope, memories]) // eslint-disable-line react-hooks/exhaustive-deps
  const past = showHistory ? all.filter((m) => m.invalid_at != null && (!query || m.content.toLowerCase().includes(query.toLowerCase()))) : []
  const byId = new Map(all.map((m) => [m.id, m]))
  const [proposals, setProposals] = useState<MemoryProposal[]>([])
  const [tidying, setTidying] = useState(false)
  const [tidyNote, setTidyNote] = useState('')
  const [labels, setLabels] = useState<Map<string, string>>(new Map())
  const proposalCount = useStore((s) => s.memoryProposals)
  const loadProposals = (): void => {
    void useStore.getState().refreshMemoryProposals()
    void api.memories.proposals(scope).then(async (ps) => {
      setProposals(ps)
      if (ps.length) setAll(await api.memories.listWithHistory(scope).catch(() => []))
      if (ps.some((p) => p.kind === 'merge_entities')) {
        const g = await api.graph.get(scope).catch(() => null)
        setLabels(new Map((g?.nodes ?? []).map((n) => [n.id, n.label])))
      }
    }).catch(() => setProposals([]))
  }
  // The count moves when auto tidy-up queues proposals in the background: re-pull the list then too.
  useEffect(() => { loadProposals() }, [scope, proposalCount]) // eslint-disable-line react-hooks/exhaustive-deps
  const tidy = async (): Promise<void> => {
    setTidying(true); setTidyNote('')
    try {
      const made = await api.memories.consolidate(targetProject)
      setTidyNote(made.length ? '' : 'Nothing to tidy up.')
      loadProposals()
    } catch { setTidyNote('Tidy up failed.') } finally { setTidying(false) }
  }
  const decide = async (p: MemoryProposal, apply: boolean): Promise<void> => {
    if (apply) await api.memories.applyProposal(p.id); else await api.memories.dismissProposal(p.id)
    loadProposals(); await refreshMemories(query)
    if (apply) { await useStore.getState().refreshGraph() }
  }
  const restore = async (id: string): Promise<void> => { await api.memories.restore(id); await refreshMemories(query); loadHistory() }

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
          : 'Personal memories go into every chat; project memories only into that project’s chats. Pinned ones are always included; the rest are chosen by recency and relevance.'}
      </p>
      <label className="muted small" style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
        <input type="checkbox" checked={showHistory} onChange={(e) => setShowHistory(e.target.checked)} /> <History size={12} /> Show history
      </label>
      <button className="primary-btn" onClick={() => void tidy()} disabled={tidying} title="Look for duplicates and stale dates. Nothing changes until you apply a suggestion.">
        <Sparkles size={14} /> {tidying ? 'Looking…' : 'Tidy up'}
        {proposalCount > 0 && <span className="count pending" title={`${proposalCount} suggestion${proposalCount === 1 ? '' : 's'} to review`}>{proposalCount}</span>}
      </button>
      {tidyNote && <span className="muted small"> {tidyNote}</span>}
      {proposals.map((p) => <ProposalRow key={p.id} p={p} byId={byId} labels={labels} onApply={() => void decide(p, true)} onDismiss={() => void decide(p, false)} />)}
      {memories.length === 0 && <p className="empty-hint big">{query ? 'No memories match.' : 'No memories yet.'}</p>}
      {memories.map((m) => <MemoryRow key={m.id} m={m} showProject={scope === 'all'} />)}
      {past.map((m) => <HistoryRow key={m.id} m={m} byId={byId} onRestore={() => void restore(m.id)} />)}
    </div>
  )
}
