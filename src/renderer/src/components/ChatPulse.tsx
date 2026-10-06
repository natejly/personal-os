import { useChatFace, useChatPulse, useUnread } from '../store'
import Face from './Face'
import type { Attention, Conversation, SessionStatus } from '@shared/types'
import { ATTENTION_LABEL } from '../lib/attention'

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
export default function ChatPulse({ conv, size = 14 }: { conv: Pick<Conversation, 'id' | 'settings' | 'project_id'>; size?: number }): JSX.Element {
  const status = useChatPulse(conv.id)
  const unread = useUnread(conv.id)
  const face = useChatFace(conv)
  return (
    <>
      <Face {...face} status={status} size={size} title={TITLE[status]} />
      {status === 'idle' && unread > 0 && <span className="pulse unread" title="Unread reply" />}
    </>
  )
}

/**
 * The one attention mark for sidebar rows (chats, desks, jobs): four colours from the app's --st-* vocabulary,
 * the state in words on hover and for screen readers. `detail` says what the state is about (a desk's status).
 */
export function AttentionDot({ state, detail }: { state: Attention; detail?: string }): JSX.Element {
  const label = detail ? `${ATTENTION_LABEL[state]}: ${detail}` : ATTENTION_LABEL[state]
  return <span className={`attn-dot attn-${state}`} role="img" title={label} aria-label={label} />
}
