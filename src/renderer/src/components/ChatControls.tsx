import { useStore, useConversation } from '../store'
import ModelMenu from './ModelMenu'

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
  const setChatModel = useStore((s) => s.setChatModel)
  const setChatSettings = useStore((s) => s.setChatSettings)
  // A canvas window always names its chat; only the page's draft reads the parked values.
  const draft = !convo && !conversationId
  const model = convo?.model ?? (draft ? draftModel : null) ?? defaultModel
  const effort = convo?.settings?.effort ?? (draft ? draftEffort : 'default')
  const fast = convo?.settings?.fast ?? (draft ? draftFast : false)
  return (
    <ModelMenu
      model={model}
      effort={effort}
      fast={!!fast}
      placement="up"
      onModel={(m) => void setChatModel(m, conversationId)}
      onEffort={(e) => void setChatSettings({ effort: e }, conversationId)}
      onFast={(f) => void setChatSettings({ fast: f }, conversationId)}
    />
  )
}
