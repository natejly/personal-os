import { PanelRight } from '../icons'
import { useStore } from '../store'
import { tip } from '../lib/tip'
import TitleBarSlot from './TitleBarSlot'

/**
 * The title-bar button that brings a hidden sidebar back. One component so every view names it the
 * same way; the button shows only while the sidebar is hidden. It is also where every title bar's `titlebar.left` items go.
 */
export default function SidebarToggle(): JSX.Element {
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const toggleSidebar = useStore((s) => s.toggleSidebar)
  return (
    <>
      {!sidebarOpen && (
        <button className="icon-btn no-drag" title={tip('Show sidebar', 'toggle-sidebar')} aria-label="Show sidebar" onClick={toggleSidebar}>
          <PanelRight size={16} />
        </button>
      )}
      <TitleBarSlot side="left" />
    </>
  )
}
