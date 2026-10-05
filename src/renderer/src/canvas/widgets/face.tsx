import { Smile } from 'lucide-react'
import { NEEDS_YOU } from '../../../../shared/types'
import { useStore } from '../../store'
import type { WidgetDef, WidgetProps } from '../registry'
import Face from '../../components/Face'

/**
 * The assistant's mood across the whole app, as one word Face knows: thinking while any chat is
 * answering, surprised while something waits on you, idle otherwise. Derived in the selector so the
 * widget only re-renders when the word changes, not on every token.
 */
const mood = (s: ReturnType<typeof useStore.getState>): string | undefined => {
  const sessions = Object.values(s.sessions)
  if (sessions.some((c) => c.streaming?.answering)) return 'streaming'
  if (sessions.some((c) => c.pendingApprovals > 0) || s.desks.some((d) => NEEDS_YOU.includes(d.status))) return 'needs_approval'
  return undefined
}

/** A window that is just the creature: no bar, no text, sized by the frame. */
function FaceWidget({ live }: WidgetProps): JSX.Element {
  const status = useStore(mood)
  return (
    <div className="face-widget">
      <Face name="Grain" status={live ? status : undefined} size="fill" title="Grain" />
    </div>
  )
}

export const def: WidgetDef = {
  kind: 'face',
  label: 'Face',
  icon: <Smile size={18} />,
  defaultSize: { w: 180, h: 180 },
  minSize: { w: 80, h: 80 },
  chrome: 'minimal',
  Component: FaceWidget
}

export default FaceWidget
