import { useEffect, useState } from 'react'
import type { Profile, ProfileHeld } from '@shared/types'
import { api } from '../../lib/api'
import { heldText } from '../../lib/presets'
import { slotNoun } from '../../lib/runtime'
import { useStore } from '../../store'
import Switch from './Switch'
import { Sec } from './shared'

const describe = (c: ProfileHeld): string =>
  c.key.startsWith('runtime:') ? `choose ${c.set} for the ${slotNoun(c.key.slice('runtime:'.length)).toLowerCase()} (${(c.diff ?? []).join('; ')})` : `change ${heldText(c)}`

/**
 * Profiles: each switch applies or reverts a bundle of defaults at once, so it is an action and not part of the Save draft.
 * A looser permission preset is held back and shown here; only the second click applies it.
 */
export default function ProfilesSection({ onApplied }: { onApplied: () => Promise<void> }): JSX.Element | null {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [held, setHeld] = useState<{ profile: Profile; changes: ProfileHeld[] } | null>(null)
  const toast = useStore((s) => s.toast)
  const refresh = async (): Promise<void> => {
    const list = await api.profiles.list()
    setProfiles(list)
    await onApplied()
  }
  useEffect(() => { api.profiles.list().then(setProfiles).catch(() => undefined) }, [])

  /** Show the switch in its new position at once: a second click before the reply must act on that, not on the old state. */
  const mark = (id: string, enabled: boolean): void => setProfiles((l) => l.map((x) => (x.id === id ? { ...x, enabled } : x)))
  const enable = async (p: Profile, confirm: boolean): Promise<void> => {
    const { held: changes } = await api.profiles.enable(p.id, confirm)
    setHeld(changes.length ? { profile: p, changes } : null)
    if (changes.length) mark(p.id, false)
    if (!changes.length) await refresh()
  }
  const toggle = (p: Profile, on: boolean): void => {
    mark(p.id, on)
    const done = on ? enable(p, false) : api.profiles.disable(p.id).then(() => { setHeld(null); return refresh() })
    done.catch((e: Error) => { mark(p.id, !on); toast(e.message, 'error') })
  }
  if (!profiles.length) return null
  return (
    <>
      <h4>Profiles</h4>
      <Sec id="profiles">
        <div className="setting-list">
          {profiles.map((p) => <Switch key={p.id} plain={false} title={p.name} label={p.name} checked={p.enabled} onChange={(on) => toggle(p, on)} />)}
        </div>
        {held && (
          <div role="alert">
            <p className="small">
              {held.profile.name} would {held.changes.map(describe).join(', and ')}, which allows more than you have now. Nothing has been applied.
            </p>
            <button className="ghost-btn" onClick={() => setHeld(null)}>Cancel</button>
            <button className="ghost-btn danger" onClick={() => enable(held.profile, true).catch((e: Error) => toast(e.message, 'error'))}>Apply anyway</button>
          </div>
        )}
      </Sec>
    </>
  )
}
