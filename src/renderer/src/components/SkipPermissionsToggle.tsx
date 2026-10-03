import { ShieldOff } from 'lucide-react'
import { useStore } from '../store'

/**
 * One switch for the chat the composer belongs to. With no conversation yet (a brand-new chat) it
 * writes the global default, so the first message inherits it. With a conversation it writes that
 * chat only: the chat's own value wins over the global one.
 */
export default function SkipPermissionsToggle({ conversationId }: { conversationId?: string }): JSX.Element {
  const convId = useStore((s) => conversationId ?? s.focusedConversationId)
  const convSkip = useStore((s) => s.sessions[conversationId ?? s.focusedConversationId ?? '']?.conversation.settings.skipPermissions)
  const globalOn = useStore((s) => !!s.settings.skipPermissions)
  const setChatSettings = useStore((s) => s.setChatSettings)
  const saveSettings = useStore((s) => s.saveSettings)

  const on = convSkip ?? globalOn
  const title = on
    ? 'Dangerously skip permissions is on. Ordinary tools run without an approval card. Deny rules, ask rules, mail and other external actions, shell commands, flagged content, repeated calls, plans and questions still ask.'
    : 'Permissions ask first. Turn this on to let tools run without an approval card in this chat.'

  return (
    <button
      className={`ghost-btn skip-perms ${on ? 'on' : ''}`}
      aria-pressed={on}
      title={title}
      onClick={() => {
        const next = !on
        if (convId) void setChatSettings({ skipPermissions: next }, convId)
        else void saveSettings({ skipPermissions: next })
      }}
    >
      <ShieldOff size={13} /> {on ? 'Skipping permissions' : 'Skip permissions'}
    </button>
  )
}
