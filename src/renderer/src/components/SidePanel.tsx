import type { ReactNode, Ref } from 'react'
import ResizeHandle from './ResizeHandle'

/**
 * The shell every right-hand panel shares: an `<aside>` with a resize handle on its left edge, an optional
 * header and a body. Its width is the `--side-panel-w` variable (default 380, 300 to 720), which the handle
 * persists for this window. Where the panel sits in a chat body it is a column beside the transcript; at or
 * under 900px (of the chat body, or of the window for the shell's own column) it floats over the transcript
 * instead. The stylesheet owns both layouts.
 */
export const SIDE_PANEL = { id: 'side-panel-w', defaultSize: 380, min: 300, max: 720 } as const

export default function SidePanel({ label, className, onClose, header, children, asideRef }: {
  /** The aside's accessible name. */
  label: string
  className?: string
  onClose: () => void
  header?: ReactNode
  children: ReactNode
  asideRef?: Ref<HTMLElement>
}): JSX.Element {
  return (
    <aside ref={asideRef} className={`side-panel${className ? ` ${className}` : ''}`} aria-label={label}>
      <ResizeHandle id={SIDE_PANEL.id} defaultSize={SIDE_PANEL.defaultSize} min={SIDE_PANEL.min} max={SIDE_PANEL.max} grows="left" onCollapse={onClose} label="Side panel width" className="at-left" />
      {header}
      {children}
    </aside>
  )
}
