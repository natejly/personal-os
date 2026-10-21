import { useEffect, useState } from 'react'
import type { RuntimeSelection, RuntimeSlot } from '@shared/types'
import { MoreHorizontal } from '../icons'
import { api } from '../lib/api'
import { confirmDiff, pickedId, slotNoun } from '../lib/runtime'
import PopMenu from './PopMenu'
import { useStore } from '../store'
import { useChatPermissionMode } from './PermissionMode'
import { pillLabel } from '../lib/permissionMode'

interface Asked { slot: string; value: RuntimeSelection; diff: string[] }

/** The slots a chat can pick a provider for: one provider at a time, and more than one to pick from. */
const pickable = (s: RuntimeSlot): boolean => !s.multi && s.providers.filter((p) => p.available).length > 1

function ChatRuntime({ conversationId, projectId }: { conversationId: string; projectId: string | null }): JSX.Element {
  const [slots, setSlots] = useState<RuntimeSlot[] | null>(null)
  const [asked, setAsked] = useState<Asked | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = (): void => { api.runtime.get(conversationId, projectId).then((v) => setSlots(v.slots.filter(pickable))).catch((e: Error) => setError(e.message)) }
  useEffect(load, [conversationId, projectId])
  const select = (slot: string, value: RuntimeSelection, confirm = false): void => {
    setError(null)
    api.runtime.select({ slot, scope: 'chat', scope_id: conversationId, value, confirm }).then(() => { setAsked(null); load() }).catch((e: unknown) => {
      const diff = confirmDiff(e)
      if (diff) setAsked({ slot, value, diff })
      else setError(e instanceof Error ? e.message : 'The selection failed.')
    })
  }
  if (asked) {
    return (
      <div role="alert">
        <p className="pop-menu-note">This would loosen something:</p>
        <ul className="pop-menu-note">{asked.diff.map((d) => <li key={d}>{d}</li>)}</ul>
        <button type="button" className="pop-menu-item" onClick={() => setAsked(null)}>Cancel</button>
        <button type="button" className="pop-menu-item" onClick={() => select(asked.slot, asked.value, true)}>Use it anyway</button>
      </div>
    )
  }
  return (
    <>
      <p className="pop-menu-title">For this chat</p>
      {error && <p className="pop-menu-note" role="status">{error}</p>}
      {slots?.map((s) => (
        <label key={s.id} className="pop-menu-field">{slotNoun(s.id)}
          <select aria-label={`${slotNoun(s.id)}, this chat`} title={s.resolution.reason} value={pickedId(s.selected.chat)} onChange={(e) => select(s.id, e.target.value || null)}>
            <option value="">As set wider</option>
            {s.providers.filter((p) => p.available).map((p) => <option key={p.id} value={p.id}>{p.label}{p.core ? '' : ` (${p.tier})`}</option>)}
          </select>
        </label>
      ))}
    </>
  )
}

/**
 * The chat's options menu beside the model chip: the permission mode (opens Settings) and, once the chat exists, which provider
 * fills each swappable seam (for now the agent loop) for this chat alone. Settings > Runtime holds the same choice for a project or everywhere.
 */
export default function ChatMenu({ conversationId, projectId }: { conversationId?: string; projectId?: string | null }): JSX.Element {
  const mode = useChatPermissionMode(conversationId ?? null)
  const openSettings = useStore((s) => s.openSettings)
  return (
    <PopMenu className="ghost-btn chat-menu" title="Chat options" aria-label="Chat options" data-testid="chat-menu" label={<MoreHorizontal size={14} aria-hidden />}>
      {(close) => (
        <>
          <button type="button" className="pop-menu-item" data-testid="chat-menu-permissions" onClick={() => { close(); openSettings('permissions') }}>Permissions: {pillLabel(mode)}</button>
          {conversationId && <ChatRuntime conversationId={conversationId} projectId={projectId ?? null} />}
        </>
      )}
    </PopMenu>
  )
}
