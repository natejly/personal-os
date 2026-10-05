import { useEffect, useMemo, useState } from 'react'
import { Plus, Pin, PinOff, Trash2, Wand2, User, History, Undo2, Sparkles, Download, Upload } from 'lucide-react'
import { useStore, type Scope } from '../store'
import type { Memory, MemoryProposal } from '@shared/types'
import ProjectChip from './ProjectChip'
import { api } from '../lib/api'
import { downloadJson, pickJson } from '../lib/jsonFile'

const KINDS = ['fact', 'preference', 'goal', 'note']

function MemoryRow({ m, showProject }: { m: Memory; showProject: boolean }): JSX.Element {
  const { updateMemory, deleteMemory, selectChat, projects } = useStore()
  const isolated = projects.some((p) => p.id === m.project_id && p.memory_mode === 'isolated')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(m.content)
  const [versions, setVersions] = useState<Memory[] | null>(null)
  const toggleVersions = (): void => {
    if (versions) setVersions(null)
    else void api.memories.history(m.id).then(setVersions).catch(() => setVersions([]))
  }
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
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); commit() } if (e.key === 'Escape') { e.preventDefault(); setDraft(m.content); setEditing(false) } }} />
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
        {versions && (versions.length < 2
          ? <p className="muted small">No earlier versions.</p>
          : versions.map((v) => <p key={v.id} className="muted small" style={v.id === m.id ? { fontWeight: 600 } : { textDecoration: 'line-through' }}>{new Date(v.created_at * 1000).toLocaleDateString()} · {v.content}</p>))}
      </div>
      <div className="mem-actions">
        <button className="icon-btn" aria-label={`Show past versions of memory: ${m.content.slice(0, 60)}`} title="Past versions" aria-pressed={!!versions} onClick={toggleVersions}><History size={14} /></button>
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
        {before.map((t, i) => <p key={i} className="mem-before">{t}</p>)}
        <p>{after}</p>
      </div>
      <div className="mem-actions">
        <button className="primary-btn sm" aria-label={`Apply: ${title}`} onClick={onApply}>Apply</button>
        <button className="ghost-btn sm" aria-label={`Dismiss: ${title}`} onClick={onDismiss}>Dismiss</button>
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
        <p className="mem-before">{m.content}</p>
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
  const allMemories = useStore((s) => s.memories)
  const focus = useStore((s) => s.memoryFocus)
  const memories = useMemo(() => (focus ? allMemories.filter((m) => focus.includes(m.id)) : allMemories), [focus, allMemories])
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

  const { toast } = useStore()
  const exportMemories = async (): Promise<void> => {
    try {
      const f = await api.memories.exportFile(scope)
      downloadJson('grain-memories.json', f)
      toast(`Exported ${f.memories.length} memor${f.memories.length === 1 ? 'y' : 'ies'}`)
    } catch (e) { toast((e as Error).message, 'error') }
  }
  const importMemories = async (): Promise<void> => {
    try {
      const file = await pickJson()
      if (!file) return
      const r = await api.memories.importFile(file, targetProject)
      toast(`Imported ${r.added}${r.skipped ? `, skipped ${r.skipped} already there` : ''}`)
      await refreshMemories(query)
    } catch (e) { toast((e as Error).message, 'error') }
  }
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
      {/* One row of secondary tools, so Add above stays the only primary action. */}
      <div className="mem-tools">
        <label className={`chip-check ${showHistory ? 'on' : ''}`} title="Also list memories that were replaced or forgotten">
          <input type="checkbox" checked={showHistory} onChange={(e) => setShowHistory(e.target.checked)} /><History size={12} /> History
        </label>
        <span className="spacer" />
        {tidyNote && <span className="muted small" role="status">{tidyNote}</span>}
        <button className="ghost-btn sm" onClick={() => void tidy()} disabled={tidying} title="Look for duplicates and stale dates. Nothing changes until you apply a suggestion.">
          <Sparkles size={13} /> {tidying ? 'Looking…' : 'Tidy up'}
          {proposalCount > 0 && <span className="count pending" title={`${proposalCount} suggestion${proposalCount === 1 ? '' : 's'} to review`}>{proposalCount}</span>}
        </button>
        <button className="ghost-btn sm" onClick={() => void exportMemories()} title="Save the memories in this scope to a JSON file"><Download size={13} /> Export</button>
        <button className="ghost-btn sm" onClick={() => void importMemories()} title={`Add memories from an exported JSON file${targetProject ? ' to this project' : ' to your personal memories'}`}><Upload size={13} /> Import</button>
      </div>
      {focus && <p className="muted small">Showing the {focus.length} memor{focus.length === 1 ? 'y' : 'ies'} from one reply. <button className="link" onClick={() => useStore.setState({ memoryFocus: null })}>Show all</button></p>}
      {proposals.map((p) => <ProposalRow key={p.id} p={p} byId={byId} labels={labels} onApply={() => void decide(p, true)} onDismiss={() => void decide(p, false)} />)}
      {memories.length === 0 && <p className="empty-hint big">{query ? 'No memories match.' : 'No memories yet.'}</p>}
      {memories.map((m) => <MemoryRow key={m.id} m={m} showProject={scope === 'all'} />)}
      {past.map((m) => <HistoryRow key={m.id} m={m} byId={byId} onRestore={() => void restore(m.id)} />)}
    </div>
  )
}
