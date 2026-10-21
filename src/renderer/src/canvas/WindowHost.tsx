import { cloneElement, useCallback, type FC, type PointerEvent as ReactPointerEvent } from 'react'
import type { CanvasWindow, WidgetKind } from '@shared/types'
import { WIDGETS, type WidgetProps } from './registry'
import { useCanvas } from './store'
import WidgetBoundary from './WidgetBoundary'

export const kindLabel = (kind: WidgetKind): string => WIDGETS[kind]?.label ?? kind

/** The registry's icon at dock size; none for a kind this build has no widget for. */
export const kindIcon = (kind: WidgetKind): JSX.Element | null => (WIDGETS[kind] ? cloneElement(WIDGETS[kind].icon, { size: 18 }) : null)

/** A row the backend accepted that this build has no widget for: still titled, still closable. */
function Unknown({ window: win }: WidgetProps): JSX.Element {
  return (
    <div className="win-stub">
      <div className="win-stub-kind">{win.kind}</div>
      <p className="muted small">No widget for this kind in this build.</p>
    </div>
  )
}

/** The catalog is the resolver; an unknown kind degrades to a placeholder instead of tearing the plane. */
const resolveWidget = (kind: WidgetKind): FC<WidgetProps> => WIDGETS[kind]?.Component ?? Unknown

export default function WindowHost({ win, focused, live }: { win: CanvasWindow; focused: boolean; live: boolean }): JSX.Element {
  const setWindowConfig = useCanvas((s) => s.setWindowConfig)
  const setWindowTitle = useCanvas((s) => s.setWindowTitle)
  const onConfig = useCallback((patch: Record<string, unknown>) => void setWindowConfig(win.id, patch), [setWindowConfig, win.id])
  const onTitle = useCallback((t: string) => void setWindowTitle(win.id, t), [setWindowTitle, win.id])
  const Body = resolveWidget(win.kind)
  // The ring lives in the title bar's `.win-status`, filled by Canvas: a body may never render chrome.
  return (
    // Keyed on what the window shows, so a caught error does not outlive a re-point at another referent.
    // This is the boundary's key, not the frame's: a raise never changes it.
    <WidgetBoundary key={win.ref_id ?? win.id} label={win.title || kindLabel(win.kind)}>
      <Body window={win} focused={focused} live={live} onConfig={onConfig} onTitle={onTitle} />
    </WidgetBoundary>
  )
}
