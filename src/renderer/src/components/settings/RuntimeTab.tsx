import { useState } from 'react'
import type { RuntimeProvider, RuntimeResolution, RuntimeSlot } from '@shared/types'
import { api } from '../../lib/api'
import { partialDimensions, slotNoun, sourceShown, sourceText, toggleSource } from '../../lib/runtime'
import { CORE, registerSettingsPanel } from '../../extensions/host'
import Switch from './Switch'
import { ScopeButtons, SelectNotices, SlotPicker } from './RuntimeParts'
import { useRuntimeSelect } from './useRuntimeSelect'

/** The agent loop is chosen in the Agent section's Loop group. */
const LOOP_SLOT = 'agent.loop'

function Enforcement({ enforcement }: { enforcement: NonNullable<RuntimeResolution['enforcement']> }): JSX.Element {
  const modes = Object.entries(enforcement)
  if (!modes.length) return <p className="set-help">Enforces nothing here, so a confined command is refused.</p>
  return (
    <table className="set-help">
      <tbody>{modes.map(([mode, dims]) => <tr key={mode}><th scope="row">{mode}</th>{Object.entries(dims).map(([d, level]) => <td key={d}>{d.replace(/_/g, ' ')}: {level}</td>)}</tr>)}</tbody>
    </table>
  )
}

/** Settings > Runtime: which provider fills each core seam, for everything, a project or a chat, and why. Nothing here is reachable by the assistant. */
export default function RuntimeTab(): JSX.Element {
  const rt = useRuntimeSelect()
  const { view, at, select, act } = rt
  const [tests, setTests] = useState<Record<string, { ok: boolean; detail: string }>>({})
  const test = (slot: string, p: RuntimeProvider): void => {
    api.runtime.test(slot, p.id).then((r) => setTests((t) => ({ ...t, [p.id]: r }))).catch((e: Error) => setTests((t) => ({ ...t, [p.id]: { ok: false, detail: e.message } })))
  }
  const accept = (p: RuntimeProvider, dimension: string, on: boolean): void => {
    const now = view?.accept_partial[p.id] ?? []
    act(api.runtime.acceptPartial(p.id, on ? [...now, dimension] : now.filter((d) => d !== dimension)))
  }

  const picker = (s: RuntimeSlot): JSX.Element => {
    if (s.multi) {
      return (
        <>
          {s.providers.filter((p) => !p.core).map((p) => (
            <Switch key={p.id} title={p.label} help={p.extension ?? undefined} checked={sourceShown(at, s.selected[at], s.resolution, p.id)} disabled={!p.available}
              onChange={(on) => select(s.id, toggleSource(s.selected[at], p.id, on))} />
          ))}
          {!s.providers.some((p) => !p.core) && <p className="set-help">No extension offers a {s.noun}.</p>}
        </>
      )
    }
    return <SlotPicker slot={s} rt={rt} />
  }

  return (
    <>
      <p className="set-help">Each seam of the app has a built-in provider, and an extension can offer another. You choose which one runs; an extension cannot choose for itself, and the permission checks, approvals and journal stay the same whichever one you pick. A chat uses its own choice first, then its project's, then a profile's, then the one set everywhere. The agent loop is chosen under Loop in the Agent section.</p>
      <ScopeButtons rt={rt} />
      <SelectNotices rt={rt} />
      {view?.slots.filter((s) => s.id !== LOOP_SLOT).map((s) => (
        <div key={s.id} className="set-sec" data-testid={`runtime-${s.id}`}>
          <h4>{slotNoun(s.id)}</h4>
          <p className="set-help" data-testid={`runtime-${s.id}-reason`}>{s.resolution.reason} <small>({sourceText(s.resolution.source)})</small>{s.resolution.locality ? ` · ${s.resolution.locality}` : ''}</p>
          {s.resolution.enforcement && s.id === 'sandbox.backend' && <Enforcement enforcement={s.resolution.enforcement} />}
          {picker(s)}
          {s.providers.filter((p) => !p.core).map((p) => (
            <div key={p.id} className="set-row">
              <b>{p.label}</b> <small>{p.tier}{p.locality ? ` · ${p.locality}` : ''}{p.available ? '' : ' · off'}</small>
              {p.available && <button type="button" className="ghost-btn sm" onClick={() => test(s.id, p)}>Test</button>}
              {tests[p.id] && <span className="set-help" role="status">{tests[p.id].ok ? 'Passed.' : `Failed: ${tests[p.id].detail}`}</span>}
              {s.id === 'sandbox.backend' && partialDimensions(p).map(({ mode, dimension }) => (
                <Switch key={`${mode}${dimension}`} title={`Accept partial ${dimension.replace(/_/g, ' ')}`} help={`${p.label} enforces ${dimension.replace(/_/g, ' ')} only partly for ${mode}; a command that needs it would otherwise be refused.`}
                  checked={(view.accept_partial[p.id] ?? []).includes(dimension)} onChange={(on) => accept(p, dimension, on)} />
              ))}
            </div>
          ))}
          {s.id === 'tools.source' && view.withheld_tools.map((t) => (
            <div key={t.slug} className="set-row">
              <b>{t.name}</b> <small>{t.source}</small>
              <span className="set-help">Its description changed since you first saw it, so it is withheld until you accept it.</span>
              <button type="button" className="ghost-btn sm" onClick={() => act(api.runtime.acceptTool(t.slug))}>Accept change</button>
            </div>
          ))}
        </div>
      ))}
    </>
  )
}

registerSettingsPanel(CORE, { id: 'runtime', label: 'Runtime', keys: 'agent loop sandbox terminal', Component: RuntimeTab, group: 'agent', order: 100 })
