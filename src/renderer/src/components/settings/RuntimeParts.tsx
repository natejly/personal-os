import type { RuntimeSlot } from '@shared/types'
import { pickedId, slotNoun } from '../../lib/runtime'
import { SCOPE_LABEL, type RuntimeSelect } from './useRuntimeSelect'
import ConfirmDanger from './ConfirmDanger'

/** The "Choose for" buttons; nothing when only Everywhere applies. */
export function ScopeButtons({ rt }: { rt: Pick<RuntimeSelect, 'scopes' | 'at' | 'setScope'> }): JSX.Element | null {
  if (rt.scopes.length < 2) return null
  return (
    <div role="group" aria-label="Choose for">
      {rt.scopes.map((s) => <button key={s} type="button" className={`ghost-btn sm${s === rt.at ? ' active' : ''}`} aria-pressed={s === rt.at} onClick={() => rt.setScope(s)}>{SCOPE_LABEL[s]}</button>)}
    </div>
  )
}

/** The error line and the "This would loosen something" confirmation of a selection. */
export function SelectNotices({ rt }: { rt: Pick<RuntimeSelect, 'error' | 'asked' | 'setAsked' | 'select'> }): JSX.Element {
  const { asked } = rt
  return (
    <>
      {rt.error && <p className="set-help" role="status">{rt.error}</p>}
      <ConfirmDanger open={!!asked} title="This would loosen something:" consequences={asked?.diff ?? []} confirmLabel="Use it anyway"
        onCancel={() => rt.setAsked(null)} onConfirm={() => { if (asked) rt.select(asked.slot, asked.value, true) }} />
    </>
  )
}

/** The select for a single-provider slot at the scope being edited. */
export function SlotPicker({ slot: s, rt }: { slot: RuntimeSlot; rt: Pick<RuntimeSelect, 'at' | 'select'> }): JSX.Element {
  const choices = s.providers.filter((p) => p.available)
  const label = s.providers.find((p) => p.id === s.default_id)?.label ?? s.default_id
  return (
    <label className="setting-row"><span className="toggle-text"><b>{SCOPE_LABEL[rt.at]}</b></span>
      <select aria-label={`${slotNoun(s.id)}, ${SCOPE_LABEL[rt.at]}`} value={pickedId(s.selected[rt.at])} disabled={choices.length < 2}
        onChange={(e) => rt.select(s.id, e.target.value || null)}>
        <option value="">{rt.at === 'global' ? `Default (${label})` : 'As set wider'}</option>
        {choices.map((p) => <option key={p.id} value={p.id}>{p.label}{p.core ? '' : ` (${p.tier})`}</option>)}
      </select>
    </label>
  )
}
