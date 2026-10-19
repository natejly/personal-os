import { sourceText } from '../../lib/settingSource'
import { useState } from 'react'
import type { RuntimeProvider, RuntimeResolution, RuntimeSlot } from '@shared/types'
import { api } from '../../lib/api'
import { partialDimensions, slotNoun, sourceShown, toggleSource } from '../../lib/runtime'
import { CORE, registerSettingsPanel } from '../../extensions/host'
import Switch from './Switch'
import { ScopeButtons, SelectNotices, SlotPicker } from './RuntimeParts'
import { useRuntimeSelect } from './useRuntimeSelect'

/** The agent loop is chosen in the Agent section's Loop group. */
const LOOP_SLOT = 'agent.loop'

function Enforcement({ enforcement }: { enforcement: NonNullable<RuntimeResolution['enforcement']> }): JSX.Element {
  const modes = Object.entries(enforcement)
  if (!modes.length) return <p className="set-help">None</p>
  return (
    <table className="set-help">
      <tbody>{modes.map(([mode, dims]) => <tr key={mode}><th scope="row">{mode}</th>{Object.entries(dims).map(([d, level]) => <td key={d}>{d.replace(/_/g, ' ')}: {level}</td>)}</tr>)}</tbody>
    </table>
  )
}

/** Settings > Runtime: which provider fills each core seam, for everything, a project or a chat. Nothing here is reachable by the assistant. */
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
            <Switch key={p.id} title={p.label} checked={sourceShown(at, s.selected[at], s.resolution, p.id)} disabled={!p.available}
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
      <ScopeButtons rt={rt} />
      <SelectNotices rt={rt} />
      {view?.slots.filter((s) => s.id !== LOOP_SLOT).map((s) => (
        <div key={s.id} className="set-sec" data-testid={`runtime-${s.id}`}>
          <h4>{slotNoun(s.id)}</h4>
          <small data-testid={`runtime-${s.id}-reason`}>{sourceText(s.resolution.source)}{s.resolution.locality ? ` · ${s.resolution.locality}` : ''}</small>
          {s.resolution.enforcement && s.id === 'sandbox.backend' && <Enforcement enforcement={s.resolution.enforcement} />}
          {picker(s)}
          {s.providers.filter((p) => !p.core).map((p) => (
            <div key={p.id} className="set-row">
              <b>{p.label}</b> <small>{p.tier}{p.locality ? ` · ${p.locality}` : ''}{p.available ? '' : ' · off'}</small>
              {p.available && <button type="button" className="ghost-btn sm" onClick={() => test(s.id, p)}>Test</button>}
              {tests[p.id] && <span className="set-help" role="status">{tests[p.id].ok ? 'Passed.' : `Failed: ${tests[p.id].detail}`}</span>}
              {s.id === 'sandbox.backend' && partialDimensions(p).map(({ mode, dimension }) => (
                <Switch key={`${mode}${dimension}`} title={`Accept partial ${dimension.replace(/_/g, ' ')}`}
                  checked={(view.accept_partial[p.id] ?? []).includes(dimension)} onChange={(on) => accept(p, dimension, on)} />
              ))}
            </div>
          ))}
          {s.id === 'tools.source' && view.withheld_tools.map((t) => (
            <div key={t.slug} className="set-row">
              <b>{t.name}</b> <small>{t.source}</small>
              <button type="button" className="ghost-btn sm" title="Description changed; withheld until accepted" onClick={() => act(api.runtime.acceptTool(t.slug))}>Accept change</button>
            </div>
          ))}
        </div>
      ))}
    </>
  )
}

registerSettingsPanel(CORE, { id: 'runtime', label: 'Runtime', keys: 'agent loop sandbox terminal', Component: RuntimeTab, group: 'agent', order: 100 })
