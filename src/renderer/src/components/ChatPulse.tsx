import { useSessionStatus } from '../store'
import type { SessionStatus } from '@shared/types'

const TITLE: Record<Exclude<SessionStatus, 'idle'>, string> = {
  working: 'Working…',
  done: 'Just finished',
  error: 'Last reply failed',
  'needs-approval': 'Waiting for your approval'
}

/**
 * Run indicator for one chat row. A component rather than a hook inside `.map`, so a row subscribes
 * to its own status string instead of the whole list re-rendering on every streamed token.
 */
export default function ChatPulse({ conversationId }: { conversationId: string }): JSX.Element | null {
  const status = useSessionStatus(conversationId)
  if (status === 'idle') return null
  return <span className={`pulse ${status}`} title={TITLE[status]} />
}
