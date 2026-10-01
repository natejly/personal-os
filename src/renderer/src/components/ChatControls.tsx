import type { Effort } from '@shared/types'
import { useStore, useConversation } from '../store'

const EFFORTS: Effort[] = ['default', 'low', 'medium', 'high']

/**
 * Model and reasoning effort, under the text box rather than above the transcript — the same quiet
 * pair on the chat page and in a canvas chat window. A draft chat has no row to PATCH, so the store
 * parks both choices (`draftModel`, `draftEffort`) and `send` applies them to the chat it creates.
 */
export default function ChatControls({ conversationId }: { conversationId?: string }): JSX.Element {
  const convo = useConversation(conversationId)
  const models = useStore((s) => s.models)
  const modelsError = useStore((s) => s.modelsError)
  const defaultModel = useStore((s) => s.settings.defaultModel)
  const draftModel = useStore((s) => s.draftModel)
  const draftEffort = useStore((s) => s.draftEffort)
  const setChatModel = useStore((s) => s.setChatModel)
  const setChatSettings = useStore((s) => s.setChatSettings)
  // A canvas window always names its chat; only the page's draft reads the parked values.
  const draft = !convo && !conversationId
  const model = convo?.model ?? (draft ? draftModel : null) ?? defaultModel
  const effort = convo?.settings?.effort ?? (draft ? draftEffort : 'default')
  const options = models.some((m) => m.id === model) ? models : [{ id: model }, ...models]
  return (
    <>
      <select className="chat-control" aria-label="Model" value={model} title={modelsError ?? 'Model (served via LiteLLM)'}
        onChange={(e) => void setChatModel(e.target.value, conversationId)}>
        {options.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
      </select>
      <select className="chat-control" aria-label="Reasoning effort" value={effort} title="Reasoning effort"
        onChange={(e) => void setChatSettings({ effort: e.target.value as Effort }, conversationId)}>
        {EFFORTS.map((x) => <option key={x} value={x}>{x === 'default' ? 'effort: default' : `effort: ${x}`}</option>)}
      </select>
    </>
  )
}
