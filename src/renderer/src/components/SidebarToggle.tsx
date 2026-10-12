import { PanelRight } from '../icons'
import { useStore } from '../store'

/**
 * The title-bar button that brings a hidden sidebar back. One component so every view names it the
 * same way; it renders nothing while the sidebar is open.
 */
export default function SidebarToggle(): JSX.Element | null {
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const toggleSidebar = useStore((s) => s.toggleSidebar)
  if (sidebarOpen) return null
  return (
    <button className="icon-btn no-drag" title="Show sidebar (⌘B)" aria-label="Show sidebar (⌘B)" onClick={toggleSidebar}>
      <PanelRight size={16} />
    </button>
  )
}
