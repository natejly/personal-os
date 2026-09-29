import { useEffect, useRef } from 'react'
import { Network } from 'lucide-react'
import { useStore, type Scope } from '../../store'
import GraphView from '../../components/GraphView'
import type { WidgetDef, WidgetProps } from '../registry'

export default function GraphWidget({ window: win, focused, live, onConfig }: WidgetProps): JSX.Element {
  const projects = useStore((s) => s.projects)
  const refreshGraph = useStore((s) => s.refreshGraph)
  const loaded = useRef(false)
  const scope = (typeof win.config.scope === 'string' ? win.config.scope : 'all') as Scope

  useEffect(() => {
    if (!live || loaded.current) return
    loaded.current = true
    void refreshGraph()
  }, [live, refreshGraph])

  // Unmounting GraphView is what kills the simulation outright; `paused` only idles it (contract §6).
  if (!live) return <div className="widget"><div className="widget-empty">Graph · paused</div></div>

  return (
    <div className="widget">
      <div className="widget-bar">
        <select className="widget-chip" title="Filter by project" value={scope} onChange={(e) => onConfig({ scope: e.target.value })}>
          <option value="all">All</option>
          <option value="personal">Personal only</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <span className="spacer" />
        {!focused && <span>paused</span>}
      </div>
      <GraphView projectId={scope} paused={!focused} />
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'graph',
  label: 'Graph',
  icon: <Network size={15} />,
  defaultSize: { w: 640, h: 560 },
  minSize: { w: 360, h: 320 },
  chrome: 'full',
  heavy: true,
  defaultConfig: { scope: 'all' },
  Component: GraphWidget
}
