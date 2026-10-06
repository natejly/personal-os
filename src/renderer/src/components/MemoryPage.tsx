import { Brain } from 'lucide-react'
import { useStore } from '../store'
import AppSwitcher from './AppSwitcher'
import SidebarToggle from './SidebarToggle'
import ScopeSelect from './ScopeSelect'
import MemoryPanel from './MemoryPanel'

/** What Grain remembers: the panel's list, voice and graph layouts under the shared scope filter. */
export default function MemoryPage(): JSX.Element {
  const libraryScope = useStore((s) => s.libraryScope)
  const setLibraryScope = useStore((s) => s.setLibraryScope)
  return (
    <main className="page memory-page">
      <header className="page-header drag">
        <SidebarToggle />
        <h2><Brain size={16} /> Memory</h2>
        <div className="knowledge-controls no-drag"><ScopeSelect value={libraryScope} onChange={(s) => void setLibraryScope(s)} /></div>
        <AppSwitcher />
      </header>
      <div className="memory-page-body"><MemoryPanel /></div>
    </main>
  )
}
