import { ShieldOff } from 'lucide-react'
import { PAGE_AGENT_DRAFT, useStore } from '../store'

/**
 * One switch for the chat the composer belongs to; the chat's own value wins over the global one.
 * A chat with no row yet (a brand-new chat, the ⌘I panel before its first message) parks the choice
 * and the send that creates the row writes it there. The global default lives in Settings only.
 */
export default function SkipPermissionsToggle({ conversationId }: { conversationId?: string }): JSX.Element {
  const convId = useStore((s) => conversationId ?? s.focusedConversationId)
  const convSkip = useStore((s) => {
    const id = conversationId ?? s.focusedConversationId
    if (id === PAGE_AGENT_DRAFT) return s.pageAgentChatSettings.skipPermissions
    return id ? s.sessions[id]?.conversation.settings.skipPermissions : s.draftChatSettings.skipPermissions
  })
  const globalOn = useStore((s) => !!s.settings.skipPermissions)
  const setChatSettings = useStore((s) => s.setChatSettings)

  const on = convSkip ?? globalOn
  // A chat with no switch of its own follows Settings → Permissions; say so, since the two look the same here.
  const source = convSkip === undefined ? ` Following the default in Settings → Permissions (${globalOn ? 'on' : 'off'}).` : ` Set for this chat; the default in Settings → Permissions is ${globalOn ? 'on' : 'off'}.`
  const title = (on
    ? 'Dangerously skip permissions is on. Ordinary tools run without an approval card. Deny rules, ask rules, mail and other external actions, shell commands, flagged content, repeated calls, plans and questions still ask.'
    : 'Permissions ask first. Turn this on to let tools run without an approval card in this chat.') + source

  return (
    <button
      className={`ghost-btn skip-perms ${on ? 'on' : ''}`}
      aria-pressed={on}
      data-inherited={convSkip === undefined || undefined}
      title={title}
      onClick={() => void setChatSettings({ skipPermissions: !on }, convId ?? undefined)}
    >
      <ShieldOff size={13} /> {on ? 'Skipping permissions' : 'Skip permissions'}
    </button>
  )
}
