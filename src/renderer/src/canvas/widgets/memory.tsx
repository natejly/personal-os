import { useEffect, useRef } from 'react'
import { Brain, Pin, PinOff, Trash2 } from 'lucide-react'
import type { Memory } from '@shared/types'
import { useStore, type Scope } from '../../store'
import { dragProps } from '../dnd'
import type { WidgetDef, WidgetProps } from '../registry'

const inScope = (m: Memory, s: Scope): boolean =>
  s === 'all' ? true : s === 'personal' ? m.project_id === null : m.project_id === s

function Row({ m }: { m: Memory }): JSX.Element {
  const updateMemory = useStore((s) => s.updateMemory)
  const deleteMemory = useStore((s) => s.deleteMemory)
  return (
    <div className={`widget-row ${m.pinned ? 'on' : ''}`} {...dragProps({ kind: 'memory', id: m.id, label: m.content, projectId: m.project_id })}>
      <div className="grow">
        <div className="widget-title" title={m.content}>{m.content}</div>
        <div className="widget-sub">{m.kind} · {m.source} · {new Date(m.updated_at * 1000).toLocaleDateString()}</div>
      </div>
      <button className="icon-btn" title={m.pinned ? 'Unpin' : 'Pin (always in context)'} onClick={() => void updateMemory(m.id, { pinned: !m.pinned })}>
        {m.pinned ? <PinOff size={13} /> : <Pin size={13} />}
      </button>
      <button className="icon-btn danger" title="Forget" onClick={() => void deleteMemory(m.id)}><Trash2 size={13} /></button>
    </div>
  )
}

export default function MemoryWidget({ window: win, live, onConfig }: WidgetProps): JSX.Element {
  const memories = useStore((s) => s.memories)
  const projects = useStore((s) => s.projects)
  const refreshMemories = useStore((s) => s.refreshMemories)
  const loaded = useRef(false)
  const scope = (typeof win.config.scope === 'string' ? win.config.scope : 'all') as Scope

  // The rows are the store's shared array, so one load per window is enough — and none at all off-screen.
  useEffect(() => {
    if (!live || loaded.current) return
    loaded.current = true
    void refreshMemories()
  }, [live, refreshMemories])

  if (!live) return <div className="widget"><div className="widget-empty">Memory · paused</div></div>

  const rows = memories.filter((m) => inScope(m, scope))
  return (
    <div className="widget">
      <div className="widget-bar">
        <select className="widget-chip" title="Filter by project" value={scope} onChange={(e) => onConfig({ scope: e.target.value })}>
          <option value="all">All</option>
          <option value="personal">Personal only</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <span className="spacer" />
        <span>{rows.length}</span>
      </div>
      {rows.length === 0 ? (
        <div className="widget-empty">No memories in this scope. Chat with auto-learn on, or add one in the Memory page.</div>
      ) : (
        <div className="widget-scroll"><div className="widget-list">{rows.map((m) => <Row key={m.id} m={m} />)}</div></div>
      )}
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'memory',
  label: 'Memory',
  icon: <Brain size={15} />,
  defaultSize: { w: 400, h: 520 },
  minSize: { w: 280, h: 240 },
  chrome: 'full',
  defaultConfig: { scope: 'all' },
  Component: MemoryWidget
}
