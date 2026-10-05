import { ChevronDown } from 'lucide-react'
import { useStore, type Scope } from '../store'

export default function ScopeSelect({ value, onChange }: { value: Scope; onChange: (s: Scope) => void }): JSX.Element {
  const projects = useStore((s) => s.projects)
  return (
    <label className="model-picker" title="Filter by project">
      <select aria-label="Project scope" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="all">All</option>
        <option value="personal">Personal only</option>
        {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select>
      <ChevronDown size={14} />
    </label>
  )
}
