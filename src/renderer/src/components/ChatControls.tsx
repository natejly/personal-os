import { DEFAULT_EFFORT } from '@shared/types'
import { useStore, useConversation } from '../store'
import ModelMenu from './ModelMenu'
import { RESPONSE_STYLES } from '../lib/responseStyle'

/**
 * Model, reasoning effort and fast mode, under the text box rather than above the transcript — the
 * same control on the chat page and in a canvas chat window. A draft chat has no row to PATCH, so the
 * store parks the choices (`draftModel`, `draftEffort`, `draftFast`) and `send` applies them to the
 * chat it creates.
 */
export default function ChatControls({ conversationId }: { conversationId?: string }): JSX.Element {
  const convo = useConversation(conversationId)
  const defaultModel = useStore((s) => s.settings.defaultModel)
  const draftModel = useStore((s) => s.draftModel)
  const draftEffort = useStore((s) => s.draftEffort)
  const draftFast = useStore((s) => s.draftFast)
  const setChatConfig = useStore((s) => s.setChatConfig)
  // A canvas window always names its chat; only the page's draft reads the parked values.
  const draft = !convo && !conversationId
  const model = convo?.model ?? (draft ? draftModel : null) ?? defaultModel
  const effort = convo?.settings?.effort ?? (draft ? draftEffort : DEFAULT_EFFORT)
  const fast = convo?.settings?.fast ?? (draft ? draftFast : false)
  const globalStyle = useStore((s) => s.settings.responseStyle) ?? 'default'
  const draftStyle = useStore((s) => s.draftChatSettings.responseStyle)
  const setChatSettings = useStore((s) => s.setChatSettings)
  const style = convo?.settings?.responseStyle ?? (draft ? draftStyle ?? globalStyle : 'default')
  return (
    <>
    <ModelMenu
      model={model}
      effort={effort}
      fast={!!fast}
      placement="up"
      onChange={(c) => void setChatConfig(c, conversationId)}
    />
    <select
      className="muted small"
      aria-label="Response style"
      title="Response style"
      value={style}
      onChange={(e) => void setChatSettings({ responseStyle: e.target.value }, conversationId)}
    >
      {RESPONSE_STYLES.map((s) => <option key={s.id} value={s.id}>{s.id === 'default' ? 'Style' : s.label}</option>)}
    </select>
    </>
  )
}
