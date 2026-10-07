import { MessageSquare } from 'lucide-react'
import { useStore } from '../store'

/** The last thing in every title bar: opens the ⌘I page agent panel, the same action as the menu item. */
export default function AppSwitcher(): JSX.Element {
  const open = useStore((s) => s.pageAgentOpen)
  const toggle = useStore((s) => s.togglePageAgent)
  return (
    <div className="app-switcher no-drag">
      <button className={`icon-btn ${open ? 'on' : ''}`} title="Quick chat (⌘I)" aria-label="Quick chat (⌘I)" aria-pressed={open} onClick={toggle}>
        <MessageSquare size={15} />
      </button>
    </div>
  )
}
