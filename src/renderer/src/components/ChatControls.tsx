import { DEFAULT_EFFORT } from '@shared/types'
import { PAGE_AGENT_DRAFT, useStore, useConversation } from '../store'
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
  const pageDraft = conversationId === PAGE_AGENT_DRAFT
  const pageModel = useStore((s) => s.pageAgentModel)
  const pageSettings = useStore((s) => s.pageAgentChatSettings)
  const setChatConfig = useStore((s) => s.setChatConfig)
  // A canvas window always names its chat; only the page's draft reads the parked values.
  const draft = !convo && !conversationId
  const model = convo?.model ?? (draft ? draftModel : pageDraft ? pageModel : null) ?? defaultModel
  const effort = convo?.settings?.effort ?? (draft ? draftEffort : pageDraft ? pageSettings.effort : null) ?? DEFAULT_EFFORT
  const fast = convo?.settings?.fast ?? (draft ? draftFast : pageDraft ? pageSettings.fast : false)
  return (
    <ModelMenu
      model={model}
      effort={effort}
      fast={!!fast}
      placement="up"
      onChange={(c) => void setChatConfig(c, conversationId)}
    />
  )
}
