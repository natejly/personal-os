import { useEffect, useRef } from 'react'
import { ListChecks } from 'lucide-react'
import { PAGE_AGENT_DRAFT, useStore } from '../store'
import '../styles/cowork.css'

type Mode = 'off' | 'auto' | 'always'

const NEXT: Record<Mode, Mode> = { off: 'auto', auto: 'always', always: 'off' }
const LABEL: Record<Mode, string> = {
  off: 'Plan mode off — the assistant acts straight away',
  auto: 'Plan mode: auto — it plans the first time it wants to change something',
  always: 'Plan mode: always — every turn drafts a plan you approve first'
}

/**
 * ⌘⇧P cycles off → auto → always. It writes `conv.settings.planMode`, never the global default: with
 * no conversation yet (a brand-new chat) the store parks the value and `send` applies it to the chat
 * it creates, so the toggle is never a no-op the user has to repeat after their first message.
 *
 * It sends ONE key: the backend's settings merge (repos.py:132-135) is shallow, which is
 * fine for a scalar and is exactly why `tools` — a nested object — has to be spread by its callers.
 */
export default function PlanModeToggle({ conversationId }: { conversationId?: string }): JSX.Element {
  const convId = useStore((s) => conversationId ?? s.focusedConversationId)
  const focused = useStore((s) => s.focusedConversationId)
  const convMode = useStore((s) => {
    // The ⌘I panel before its first message parks the mode for the thread its send creates.
    if (conversationId === PAGE_AGENT_DRAFT) return s.pageAgentChatSettings.planMode ?? null
    const id = conversationId ?? s.focusedConversationId
    return (id ? s.sessions[id]?.conversation.settings.planMode : s.draftChatSettings.planMode) ?? null
  })
  const globalMode = useStore((s) => s.settings.planMode ?? 'off')
  const setChatSettings = useStore((s) => s.setChatSettings)

  const mode: Mode = convMode ?? globalMode

  const cycle = useRef(() => {})
  cycle.current = () => {
    void setChatSettings({ planMode: NEXT[mode] }, convId ?? undefined)
  }

  // Only the composer the user is actually looking at owns the shortcut: several chat widgets can be
  // mounted at once, and all of them reacting to one ⌘⇧P would toggle the mode N times.
  const owns = conversationId === undefined || conversationId === focused
  useEffect(() => {
    if (!owns) return
    const onKey = (e: KeyboardEvent): void => {
      if (!(e.metaKey || e.ctrlKey) || !e.shiftKey) return
      if (e.key.toLowerCase() !== 'p') return
      e.preventDefault()
      cycle.current()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [owns])

  return (
    <button
      className={`composer-ctl plan-mode ${mode}`}
      aria-pressed={mode !== 'off'}
      data-inherited={convMode === null || undefined}
      title={`${LABEL[mode]} (⌘⇧P). ${convMode === null ? `Following the default in Settings → Permissions (${globalMode}).` : `Set for this chat; the default in Settings → Permissions is ${globalMode}.`}`}
      onClick={() => cycle.current()}
    >
      <ListChecks size={13} /> Plan{mode === 'off' ? '' : `: ${mode}`}
    </button>
  )
}
