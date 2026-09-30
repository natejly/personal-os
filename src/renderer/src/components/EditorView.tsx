import { PanelLeftOpen, PenLine } from 'lucide-react'
import { useStore } from '../store'

/** Placeholder shell for the document editor. The real editor replaces this body wholesale. */
export default function EditorView(): JSX.Element {
  const editorDocId = useStore((s) => s.editorDocId)
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const toggleSidebar = useStore((s) => s.toggleSidebar)
  return (
    <main className="page">
      <header className="page-header drag">
        {!sidebarOpen && <button className="icon-btn no-drag" onClick={toggleSidebar}><PanelLeftOpen size={16} /></button>}
        <h2><PenLine size={16} /> Editor</h2>
      </header>
      <div className="page-body">
        <p className="empty-hint big">{editorDocId ? `Document ${editorDocId}` : 'No document open.'}</p>
      </div>
    </main>
  )
}
