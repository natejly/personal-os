import { useChatPulse, useUnread } from '../store'
import Face from './Face'
import type { SessionStatus } from '@shared/types'

const TITLE: Partial<Record<SessionStatus, string>> = {
  working: 'Working…',
  done: 'Just finished',
  error: 'Last reply failed',
  'needs-approval': 'Waiting for your approval'
}

/**
 * One chat row's face, posed by its run status: a working chat breathes and blinks (Face animates live
 * statuses), so there is no separate blinking dot. A component rather than a hook inside `.map`, so a
 * row subscribes to its own status string instead of the whole list re-rendering on every streamed token.
 * Idle with something to read: a still dot, because the reply finished while this chat was out of sight.
 */
export default function ChatPulse({ conversationId, size = 14 }: { conversationId: string; size?: number }): JSX.Element {
  const status = useChatPulse(conversationId)
  const unread = useUnread(conversationId)
  return (
    <>
      <Face name={conversationId} status={status} size={size} title={TITLE[status]} />
      {status === 'idle' && unread > 0 && <span className="pulse unread" title="Unread reply" />}
    </>
  )
}
