import { useEffect, useMemo, useRef, useState } from 'react'
import { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide, forceX, forceY, type Simulation, type SimulationNodeDatum, type SimulationLinkDatum } from 'd3-force'
import { Plus, Trash2, Globe, X, Link2, Maximize2 } from 'lucide-react'
import { useStore, type Scope } from '../store'
import { api } from '../lib/api'
import type { GraphEdge, GraphNode } from '@shared/types'

interface SimNode extends SimulationNodeDatum { id: string; label: string; type: string; global: boolean; degree: number }
interface SimLink extends SimulationLinkDatum<SimNode> { id: string; relation: string }

const TYPE_COLORS: Record<string, string> = {
  person: '#3b9edb', project: '#d97757', organization: '#8e6fdb', tool: '#46a758', place: '#e5a13b', concept: '#d95c9e', entity: '#8b8b8b', other: '#8b8b8b'
}
const colorFor = (t: string): string => TYPE_COLORS[t] ?? '#8b8b8b'
const TYPES = Object.keys(TYPE_COLORS).filter((t) => t !== 'entity')

function NodePanel({ node, onClose }: { node: GraphNode; onClose: () => void }): JSX.Element {
  const graph = useStore((s) => s.graph)
  const refreshGraph = useStore((s) => s.refreshGraph)
  const refreshProjects = useStore((s) => s.refreshProjects)
  const toast = useStore((s) => s.toast)
  const projectId = node.project_id
  const [label, setLabel] = useState(node.label)
  const [type, setType] = useState(node.type)
  const [props, setProps] = useState(JSON.stringify(node.properties, null, 2))
  const [target, setTarget] = useState('')
  const [relation, setRelation] = useState('')

  useEffect(() => { setLabel(node.label); setType(node.type); setProps(JSON.stringify(node.properties, null, 2)) }, [node])

  const byId = useMemo(() => Object.fromEntries(graph.nodes.map((n) => [n.id, n])), [graph.nodes])
  const edges = graph.edges.filter((e) => e.source_id === node.id || e.target_id === node.id)

  const save = async (): Promise<void> => {
    let properties: Record<string, unknown> | undefined
    try { properties = props.trim() ? JSON.parse(props) : {} } catch { return toast('Properties must be valid JSON', 'error') }
    await api.graph.updateNode(node.id, { label, type, properties })
    await refreshGraph()
  }
  const addEdge = async (): Promise<void> => {
    const t = graph.nodes.find((n) => n.label.toLowerCase() === target.trim().toLowerCase())
    if (!relation.trim() || !target.trim()) return
    const targetNode = t ?? (await api.graph.createNode({ project_id: projectId, label: target.trim() }))
    await api.graph.createEdge({ project_id: projectId, source_id: node.id, target_id: targetNode.id, relation: relation.trim() })
    setTarget(''); setRelation('')
    await Promise.all([refreshGraph(), refreshProjects()])
  }
  const del = async (): Promise<void> => {
    await api.graph.deleteNode(node.id)
    onClose()
    await Promise.all([refreshGraph(), refreshProjects()])
  }

  return (
    <aside className="node-panel">
      <header>
        <span className="project-dot sm" style={{ background: colorFor(type) }} />
        <h3>{node.label}</h3>
        {node.project_id === null && <span className="tag global"><Globe size={10} />personal</span>}
        <button className="icon-btn" aria-label={`Close details for ${node.label}`} onClick={onClose}><X size={16} /></button>
      </header>
      <label><span>Label</span><input value={label} onChange={(e) => setLabel(e.target.value)} onBlur={() => void save()} /></label>
      <label><span>Type</span>
        <select value={type} onChange={(e) => { setType(e.target.value); void api.graph.updateNode(node.id, { type: e.target.value }).then(refreshGraph) }}>
          {[...new Set(['entity', ...TYPES, type])].map((t) => <option key={t}>{t}</option>)}
        </select>
      </label>
      <label><span>Properties (JSON)</span><textarea rows={3} value={props} onChange={(e) => setProps(e.target.value)} onBlur={() => void save()} spellCheck={false} /></label>

      <h4>Relations <span>{edges.length}</span></h4>
      <ul className="edge-list">
        {edges.map((e) => {
          const out = e.source_id === node.id
          const other = byId[out ? e.target_id : e.source_id]
          return (
            <li key={e.id}>
              <span className="dir">{out ? '→' : '←'}</span>
              <input className="rel" aria-label={`Relation ${out ? 'to' : 'from'} ${other?.label ?? 'unknown entity'}`} defaultValue={e.relation} onBlur={(ev) => ev.target.value !== e.relation && void api.graph.updateEdge(e.id, { relation: ev.target.value }).then(refreshGraph)} />
              <span className="other">{other?.label ?? '?'}</span>
              <button className="icon-btn ghost danger" aria-label={`Delete relation "${e.relation}" ${out ? 'to' : 'from'} ${other?.label ?? 'unknown entity'}`} onClick={() => void api.graph.deleteEdge(e.id).then(refreshGraph)}><Trash2 size={12} /></button>
            </li>
          )
        })}
      </ul>
      <div className="add-edge">
        <Link2 size={13} />
        <input placeholder="relation (e.g. works on)" value={relation} onChange={(e) => setRelation(e.target.value)} />
        <input list="node-labels" placeholder="target entity" value={target} onChange={(e) => setTarget(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void addEdge()} />
        <datalist id="node-labels">{graph.nodes.filter((n) => n.id !== node.id).map((n) => <option key={n.id} value={n.label} />)}</datalist>
        <button className="icon-btn" aria-label="Add relation" title="Add relation (creates the target if new)" onClick={() => void addEdge()}><Plus size={14} /></button>
      </div>
      <button className="ghost-btn danger full" onClick={() => void del()}><Trash2 size={14} /> Delete entity and its relations</button>
    </aside>
  )
}

/**
 * The knowledge-graph half of the Memory panel. The panel owns the scope filter
 * and the search box and passes the query down; this component renders only the
 * canvas, its floating tools and the selected-node inspector.
 * `paused` idles the force simulation without tearing it down — a canvas window
 * that is not focused keeps its layout but stops burning frames on it.
 */
export default function GraphView({ projectId: scopedProjectId, query = '', paused = false }: { projectId?: string; query?: string; paused?: boolean }): JSX.Element {
  const graph = useStore((s) => s.graph)
  const libraryScope = useStore((s) => s.libraryScope)
  // Selectors, not `useStore()`: a bare subscription re-renders the whole SVG on every streamed token.
  const refreshGraph = useStore((s) => s.refreshGraph)
  const refreshProjects = useStore((s) => s.refreshProjects)
  const scope: Scope = scopedProjectId ?? libraryScope
  const projectId = scope === 'all' || scope === 'personal' ? null : scope
  const wrapRef = useRef<HTMLDivElement>(null)
  const simRef = useRef<Simulation<SimNode, SimLink> | null>(null)
  const nodesRef = useRef<SimNode[]>([])
  const linksRef = useRef<SimLink[]>([])
  const [, setTick] = useState(0)
  const [size, setSize] = useState({ w: 800, h: 600 })
  const [view, setView] = useState({ x: 0, y: 0, k: 1 })
  const [selected, setSelected] = useState<string | null>(null)
  const [newLabel, setNewLabel] = useState('')
  const dragging = useRef<{ node?: SimNode; panStart?: { x: number; y: number; vx: number; vy: number } } | null>(null)
  // Read by the rebuild effect, which must not itself depend on `paused` — a rebuild would hand the
  // in-flight node drag a stale SimNode.
  const pausedRef = useRef(paused)

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    setSize({ w: el.clientWidth, h: el.clientHeight })
    return () => ro.disconnect()
  }, [])

  // (Re)build the simulation when graph data changes, preserving positions.
  useEffect(() => {
    const prev = Object.fromEntries(nodesRef.current.map((n) => [n.id, n]))
    const degree: Record<string, number> = {}
    for (const e of graph.edges) { degree[e.source_id] = (degree[e.source_id] ?? 0) + 1; degree[e.target_id] = (degree[e.target_id] ?? 0) + 1 }
    const nodes: SimNode[] = graph.nodes.map((n) => ({
      ...(prev[n.id] ?? { x: size.w / 2 + (Math.random() - 0.5) * 200, y: size.h / 2 + (Math.random() - 0.5) * 200 }),
      id: n.id, label: n.label, type: n.type, global: n.project_id === null, degree: degree[n.id] ?? 0
    }))
    const ids = new Set(nodes.map((n) => n.id))
    const links: SimLink[] = graph.edges.filter((e) => ids.has(e.source_id) && ids.has(e.target_id)).map((e) => ({ id: e.id, relation: e.relation, source: e.source_id, target: e.target_id }))
    nodesRef.current = nodes
    linksRef.current = links
    simRef.current?.stop()
    simRef.current = forceSimulation<SimNode, SimLink>(nodes)
      .force('link', forceLink<SimNode, SimLink>(links).id((d) => d.id).distance(110).strength(0.6))
      // Ranged charge plus x/y gravity: most entities have no edge, and an unbounded repulsion with only
      // a weak centring force flung those loose nodes past the edges of the pane.
      .force('charge', forceManyBody().strength(-320).distanceMax(320))
      .force('center', forceCenter(size.w / 2, size.h / 2).strength(0.05))
      .force('x', forceX<SimNode>(size.w / 2).strength(0.07))
      .force('y', forceY<SimNode>(size.h / 2).strength(0.07))
      .force('collide', forceCollide<SimNode>().radius((d) => 22 + d.degree * 2))
      .alpha(prev && Object.keys(prev).length ? 0.5 : 1)
      .on('tick', () => setTick((t) => t + 1))
    if (pausedRef.current) {
      simRef.current.stop()
      // A cold graph built while paused has random positions; settle it once instead of ticking on.
      if (Object.keys(prev).length === 0) { simRef.current.tick(120); setTick((t) => t + 1) }
    }
    return () => { simRef.current?.stop() }
  }, [graph, size.w, size.h])

  useEffect(() => {
    const sim = simRef.current
    if (!sim) return
    if (paused) sim.stop()
    else if (pausedRef.current) sim.alpha(0.3).restart()
    pausedRef.current = paused
  }, [paused])

  const toWorld = (cx: number, cy: number): { x: number; y: number } => ({ x: (cx - view.x) / view.k, y: (cy - view.y) / view.k })

  const onWheel = (e: React.WheelEvent): void => {
    const rect = wrapRef.current!.getBoundingClientRect()
    const cx = e.clientX - rect.left, cy = e.clientY - rect.top
    const k = Math.min(3, Math.max(0.25, view.k * (e.deltaY < 0 ? 1.1 : 0.9)))
    setView({ k, x: cx - ((cx - view.x) / view.k) * k, y: cy - ((cy - view.y) / view.k) * k })
  }
  const onPointerDown = (e: React.PointerEvent, node?: SimNode): void => {
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId)
    if (node) {
      dragging.current = { node }
      node.fx = node.x; node.fy = node.y
      simRef.current?.alphaTarget(0.3).restart()
    } else {
      dragging.current = { panStart: { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y } }
    }
  }
  const onPointerMove = (e: React.PointerEvent): void => {
    const d = dragging.current
    if (!d) return
    const rect = wrapRef.current!.getBoundingClientRect()
    if (d.node) {
      const p = toWorld(e.clientX - rect.left, e.clientY - rect.top)
      d.node.fx = p.x; d.node.fy = p.y
    } else if (d.panStart) {
      setView((v) => ({ ...v, x: d.panStart!.vx + (e.clientX - d.panStart!.x), y: d.panStart!.vy + (e.clientY - d.panStart!.y) }))
    }
  }
  const onPointerUp = (): void => {
    const d = dragging.current
    if (d?.node) { d.node.fx = null; d.node.fy = null; simRef.current?.alphaTarget(0) }
    dragging.current = null
  }

  const addNode = async (): Promise<void> => {
    if (!newLabel.trim()) return
    const n = await api.graph.createNode({ project_id: projectId, label: newLabel.trim() })
    setNewLabel('')
    await Promise.all([refreshGraph(), refreshProjects()])
    setSelected(n.id)
  }

  const ql = query.trim().toLowerCase()
  const matches = ql ? new Set(nodesRef.current.filter((n) => n.label.toLowerCase().includes(ql)).map((n) => n.id)) : null
  const selectedNode = graph.nodes.find((n) => n.id === selected) ?? null
  const neighbors = useMemo(() => {
    if (!selected) return null
    const s = new Set([selected])
    for (const e of graph.edges) { if (e.source_id === selected) s.add(e.target_id); if (e.target_id === selected) s.add(e.source_id) }
    return s
  }, [selected, graph.edges])
  const presentTypes = [...new Set(graph.nodes.map((n) => n.type))]

  return (
    <div className="graph-body">
      <div className="graph-canvas" ref={wrapRef} onWheel={onWheel} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerLeave={onPointerUp}>
        <div className="graph-tools">
          <input placeholder="New entity" value={newLabel} onChange={(e) => setNewLabel(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void addNode()} />
          <button className="icon-btn" aria-label="Add entity" title="Add entity (Enter)" onClick={() => void addNode()}><Plus size={15} /></button>
          <span className="sep" />
          <button className="icon-btn" aria-label="Reset graph view" title="Reset view" onClick={() => setView({ x: 0, y: 0, k: 1 })}><Maximize2 size={15} /></button>
        </div>
        {graph.nodes.length === 0 && <p className="empty-hint big center">No entities yet. Chat with auto-learn on, or add one here.</p>}
        <svg width={size.w} height={size.h} onPointerDown={(e) => { if (e.target === e.currentTarget) { setSelected(null); onPointerDown(e) } }}>
          <defs><marker id="arrow" viewBox="0 0 10 10" refX="22" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--text-faint)" /></marker></defs>
          <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
            {linksRef.current.map((l) => {
              const s = l.source as SimNode, t = l.target as SimNode
              if (s.x === undefined || t.x === undefined) return null
              const dim = neighbors ? !(neighbors.has(s.id) && neighbors.has(t.id) && (s.id === selected || t.id === selected)) : false
              return (
                <g key={l.id} className={`link ${dim ? 'dim' : ''}`}>
                  <line x1={s.x} y1={s.y} x2={t.x} y2={t.y} markerEnd="url(#arrow)" />
                  <text x={(s.x! + t.x!) / 2} y={(s.y! + t.y!) / 2 - 4} textAnchor="middle">{l.relation}</text>
                </g>
              )
            })}
            {nodesRef.current.map((n) => {
              const r = 10 + Math.min(10, n.degree * 1.5)
              const dim = (matches && !matches.has(n.id)) || (neighbors && !neighbors.has(n.id))
              return (
                <g key={n.id} className={`node ${n.id === selected ? 'selected' : ''} ${dim ? 'dim' : ''}`} transform={`translate(${n.x ?? 0},${n.y ?? 0})`}
                  onPointerDown={(e) => { e.stopPropagation(); setSelected(n.id); onPointerDown(e, n) }}>
                  <circle r={r} fill={colorFor(n.type)} strokeDasharray={n.global ? '3 2' : undefined} />
                  <text y={r + 13} textAnchor="middle">{n.label}</text>
                </g>
              )
            })}
          </g>
        </svg>
        <div className="legend">
          {presentTypes.map((t) => <span key={t}><i style={{ background: colorFor(t) }} />{t}</span>)}
          {scope === 'all' && graph.nodes.some((n) => n.project_id === null) && <span><i className="dashed" />personal</span>}
        </div>
      </div>
      {selectedNode && <NodePanel node={selectedNode} onClose={() => setSelected(null)} />}
    </div>
  )
}
