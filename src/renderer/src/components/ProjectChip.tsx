import type { CSSProperties } from 'react'
import { Globe } from 'lucide-react'
import { useProject, useStore } from '../store'

/** Small coloured label naming the project an item belongs to (or "personal"). */
export default function ProjectChip({ projectId, clickable = true, showPersonal = false }: { projectId: string | null; clickable?: boolean; showPersonal?: boolean }): JSX.Element | null {
  const project = useProject(projectId)
  const openProject = useStore((s) => s.openProject)
  if (!projectId) return showPersonal ? <span className="tag global"><Globe size={10} />personal</span> : null
  if (!project) return null
  return (
    // The dot carries the project colour as is; the label only leans toward it (see `.project-tag`), because
    // a pastel that reads on the dark theme is close to invisible as text on the light one.
    <button className="tag project-tag" style={{ '--chip': project.color } as CSSProperties} onClick={(e) => { if (!clickable) return; e.stopPropagation(); openProject(project.id) }} disabled={!clickable}>
      <span className="project-dot sm" style={{ background: project.color }} />{project.name}
    </button>
  )
}
