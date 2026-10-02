import { useEffect, useRef } from 'react'
import { ListChecks } from 'lucide-react'
import { useStore } from '../store'
import '../styles/cowork.css'

type Mode = 'off' | 'auto' | 'always'

const NEXT: Record<Mode, Mode> = { off: 'auto', auto: 'always', always: 'off' }
const LABEL: Record<Mode, string> = {
  off: 'Plan mode off — the assistant acts straight away',
  auto: 'Plan mode: auto — it plans the first time it wants to change something',
  always: 'Plan mode: always — every turn drafts a plan you approve first'
}

/**
 * ⌘⇧P cycles off → auto → always. With a conversation this writes `conv.settings.planMode`; with no
 * conversation yet (a brand-new chat) it writes the global default instead, so the toggle is never a
 * no-op the user has to repeat after their first message.
 *
 * `setPlanMode` sends ONE key: the backend's settings merge (repos.py:132-135) is shallow, which is
 * fine for a scalar and is exactly why `tools` — a nested object — has to be spread by its callers.
 */
export default function PlanModeToggle({ conversationId }: { conversationId?: string }): JSX.Element {
  const convId = useStore((s) => conversationId ?? s.focusedConversationId)
  const focused = useStore((s) => s.focusedConversationId)
  const convMode = useStore((s) => s.sessions[conversationId ?? s.focusedConversationId ?? '']?.conversation.settings.planMode ?? null)
  const globalMode = useStore((s) => s.settings.planMode ?? 'off')
  const setPlanMode = useStore((s) => s.setPlanMode)
  const saveSettings = useStore((s) => s.saveSettings)

  const mode: Mode = convMode ?? globalMode

  const cycle = useRef(() => {})
  cycle.current = () => {
    const next = NEXT[mode]
    if (convId) void setPlanMode(convId, next)
    else void saveSettings({ planMode: next })
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
      title={`${LABEL[mode]} (⌘⇧P)`}
      onClick={() => cycle.current()}
    >
      <ListChecks size={13} /> Plan{mode === 'off' ? '' : `: ${mode}`}
    </button>
  )
}
