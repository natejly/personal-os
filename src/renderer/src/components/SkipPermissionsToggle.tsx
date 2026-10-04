import { ShieldOff } from 'lucide-react'
import { useStore } from '../store'

/**
 * One switch for the chat the composer belongs to. It never writes the global default: with no
 * conversation yet (a brand-new chat) the store parks the value and `send` applies it to the chat it
 * creates. Shown: the chat's (or the draft's) own value, else the global default.
 */
export default function SkipPermissionsToggle({ conversationId }: { conversationId?: string }): JSX.Element {
  const convId = useStore((s) => conversationId ?? s.focusedConversationId)
  const convSkip = useStore((s) => {
    const id = conversationId ?? s.focusedConversationId
    return id ? s.sessions[id]?.conversation.settings.skipPermissions : s.draftChatSettings.skipPermissions
  })
  const globalOn = useStore((s) => !!s.settings.skipPermissions)
  const setChatSettings = useStore((s) => s.setChatSettings)

  const on = convSkip ?? globalOn
  const title = on
    ? 'Dangerously skip permissions is on. Ordinary tools run without an approval card. Deny rules, ask rules, mail and other external actions, shell commands, flagged content, repeated calls, plans and questions still ask.'
    : 'Permissions ask first. Turn this on to let tools run without an approval card in this chat.'

  return (
    <button
      className={`ghost-btn skip-perms ${on ? 'on' : ''}`}
      aria-pressed={on}
      title={title}
      onClick={() => void setChatSettings({ skipPermissions: !on }, convId ?? undefined)}
    >
      <ShieldOff size={13} /> {on ? 'Skipping permissions' : 'Skip permissions'}
    </button>
  )
}
