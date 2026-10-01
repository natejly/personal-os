import { Check, ListChecks, Loader2, Lock, TriangleAlert } from 'lucide-react'
import type { SessionStatus } from '@shared/types'
import { useRingStatus } from './useRingStatus'
// Also imported by registry.ts; a ring rendered by the frame alone must still bring its own styles.
import '../styles/widgets.css'

const LABEL: Record<Exclude<SessionStatus, 'idle'>, string> = {
  working: 'Working…',
  done: 'Just finished',
  error: 'Last reply failed',
  'needs-approval': 'Waiting for your approval',
  'awaiting-plan': 'Waiting for you to approve a plan'
}

/** The status as a shape. Colour is decoration; this is what a colour-blind user actually reads. */
export const StatusGlyph = ({ status, size = 11 }: { status: SessionStatus; size?: number }): JSX.Element | null => {
  switch (status) {
    case 'working':
      return <span className="status-glyph working"><Loader2 size={size} strokeWidth={2.5} /></span>
    case 'done':
      return <span className="status-glyph done"><Check size={size} strokeWidth={3} /></span>
    case 'error':
      return <span className="status-glyph error"><TriangleAlert size={size} strokeWidth={2.5} /></span>
    case 'needs-approval':
      return <span className="status-glyph needs-approval"><Lock size={size} strokeWidth={2.5} /></span>
    // Without a case here the ring renders as an empty span: the default returns null.
    case 'awaiting-plan':
      return <span className="status-glyph awaiting-plan"><ListChecks size={size} strokeWidth={2.5} /></span>
    default:
      return null
  }
}

export interface StatusRingProps {
  /** the window's conversation; a window with none renders nothing */
  conversationId: string | null | undefined
  /** glyph size: 11 suits a 30 px title bar, 12 a 44 px dock tile */
  size?: number
}

/**
 * Title-bar status marker, and — via `.win:has(.ring.…)` / `.dock-tile:has(.ring.…)` in widgets.css —
 * the 2 px ring on the window or tile that contains it. Subscribes per conversation like `ChatPulse`,
 * so a streaming chat never re-renders its neighbours.
 */
export const StatusRing = ({ conversationId, size }: StatusRingProps): JSX.Element | null => {
  const { status, approvals } = useRingStatus(conversationId)
  if (status === 'idle') return null
  return (
    <span className={`ring ${status}`} role="status" aria-label={LABEL[status]} title={LABEL[status]}>
      <StatusGlyph status={status} size={size} />
      {status === 'needs-approval' && approvals > 0 && <span className="ring-badge">{approvals}</span>}
    </span>
  )
}

export default StatusRing
