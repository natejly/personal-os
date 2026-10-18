import { useEffect, useState } from 'react'
import type { RuntimeSelection, RuntimeSlot } from '@shared/types'
import { SlidersHorizontal } from '../icons'
import { api } from '../lib/api'
import { confirmDiff, pickedId, slotNoun } from '../lib/runtime'
import PopMenu from './PopMenu'

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
          <select aria-label={`${slotNoun(s.id)}, this chat`} value={pickedId(s.selected.chat)} onChange={(e) => select(s.id, e.target.value || null)}>
            <option value="">As set wider</option>
            {s.providers.filter((p) => p.available).map((p) => <option key={p.id} value={p.id}>{p.label}{p.core ? '' : ` (${p.tier})`}</option>)}
          </select>
          <small>{s.resolution.reason}</small>
        </label>
      ))}
    </>
  )
}

/** The chat's runtime menu: which provider fills each swappable seam (for now the agent loop) for this chat alone. Settings > Runtime
    holds the same choice for a project or everywhere. */
export default function RuntimeMenu({ conversationId, projectId }: { conversationId: string; projectId: string | null }): JSX.Element {
  return (
    <PopMenu className="ghost-btn" title="Runtime for this chat" aria-label="Runtime for this chat" label={<SlidersHorizontal size={13} aria-hidden />}>
      {() => <ChatRuntime conversationId={conversationId} projectId={projectId} />}
    </PopMenu>
  )
}
