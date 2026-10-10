import { Zap } from 'lucide-react'
import { useStore } from '../store'

/** Whether quick answer mode is on for a chat: its stored setting, or the draft's parked one before the first send. */
export const useQuick = (conversationId?: string): boolean =>
  useStore((s) => {
    const id = conversationId ?? s.focusedConversationId
    return (id ? s.sessions[id]?.conversation.settings?.quick : s.draftChatSettings.quick) === true
  })

/** Composer switch for quick answer mode: short answers, no background work. Also `/quick`. */
export default function QuickToggle({ conversationId }: { conversationId?: string }): JSX.Element {
  const on = useQuick(conversationId)
  const setChatSettings = useStore((s) => s.setChatSettings)
  const title = on ? 'Quick answers on: short replies, no delegation or background jobs' : 'Quick answers: short replies, no delegation or background jobs'
  return (
    <button type="button" className={`composer-ctl ${on ? 'on' : ''}`} aria-pressed={on} title={title} aria-label={title}
      onClick={() => void setChatSettings({ quick: !on }, conversationId)}>
      <Zap size={13} /> Quick
    </button>
  )
}
