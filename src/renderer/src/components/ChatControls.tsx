import { DEFAULT_EFFORT } from '@shared/types'
import { useStore, useConversation } from '../store'
import { simpleOn } from '../lib/simpleMode'
import DuelControls from './DuelControls'
import ModelMenu from './ModelMenu'
import ChatMenu from './ChatMenu'

/**
 * Model, effort and fast mode, under the text box rather than above the transcript — the
 * same control on the chat page and in a canvas chat window. A draft chat has no row to PATCH, so the
 * store parks the choices (`draftModel`, `draftEffort`, `draftFast`) and `send` applies them to the
 * chat it creates.
 */
export default function ChatControls({ conversationId }: { conversationId?: string }): JSX.Element {
  const convo = useConversation(conversationId)
  const simple = useStore((s) => simpleOn(s.settings))
  const defaultModel = useStore((s) => s.settings.defaultModel)
  const draftModel = useStore((s) => s.draftModel)
  const draftEffort = useStore((s) => s.draftEffort)
  const draftFast = useStore((s) => s.draftFast)
  const setChatConfig = useStore((s) => s.setChatConfig)
  // A canvas window always names its chat.
  const draft = !convo && !conversationId
  const model = convo?.model ?? (draft ? draftModel : null) ?? defaultModel
  const effort = convo?.settings?.effort ?? (draft ? draftEffort : null) ?? DEFAULT_EFFORT
  const fast = convo?.settings?.fast ?? (draft ? draftFast : false)
  return (
    <>
      {!simple && (
        <ModelMenu
          model={model}
          effort={effort}
          fast={!!fast}
          placement="up"
          onChange={(c) => void setChatConfig(c, conversationId)}
        />
      )}
      <ChatMenu conversationId={convo?.id ?? conversationId} projectId={convo?.project_id} />
      {/* Only the main chat draws a duel's columns; a window never offers one. */}
      {!conversationId && <DuelControls />}
    </>
  )
}
