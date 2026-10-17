import { useCallback, useEffect, useState } from 'react'
import type { RuntimeProvider, RuntimeResolution, RuntimeSelection, RuntimeSlot, RuntimeView } from '@shared/types'
import { api } from '../../lib/api'
import { confirmDiff, partialDimensions, pickedId, slotNoun, sourceOn, sourceText, toggleSource } from '../../lib/runtime'
import { CORE, registerSettingsPanel } from '../../extensions/host'
import { useStore } from '../../store'
import Switch from './Switch'

type Scope = 'global' | 'project' | 'chat'
interface Asked { slot: string; value: RuntimeSelection; diff: string[] }

const SCOPE_LABEL: Record<Scope, string> = { global: 'Everywhere', project: 'This project', chat: 'This chat' }

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
  const conversationId = useStore((s) => s.focusedConversationId)
  const projectId = useStore((s) => (s.focusedConversationId ? s.sessions[s.focusedConversationId]?.conversation.project_id : null) ?? null)
  const [view, setView] = useState<RuntimeView | null>(null)
  const [scope, setScope] = useState<Scope>('global')
  const [asked, setAsked] = useState<Asked | null>(null)
  const [tests, setTests] = useState<Record<string, { ok: boolean; detail: string }>>({})
  const [error, setError] = useState<string | null>(null)
  const load = useCallback((): void => {
    api.runtime.get(conversationId, projectId).then(setView).catch((e: Error) => setError(e.message))
  }, [conversationId, projectId])
  useEffect(load, [load])
  const scopes: Scope[] = ['global', ...(projectId ? ['project' as const] : []), ...(conversationId ? ['chat' as const] : [])]
  const at = scopes.includes(scope) ? scope : 'global'

  const select = (slot: string, value: RuntimeSelection, confirm = false): void => {
    setError(null)
    const scope_id = at === 'chat' ? conversationId : at === 'project' ? projectId : null
    api.runtime.select({ slot, scope: at, scope_id, value, confirm }).then(() => { setAsked(null); load() }).catch((e: unknown) => {
      const diff = confirmDiff(e)
      if (diff) setAsked({ slot, value, diff })
      else setError(e instanceof Error ? e.message : 'The selection failed.')
    })
  }
  const act = (p: Promise<unknown>): void => { setError(null); p.then(load).catch((e: Error) => setError(e.message)) }
  const test = (slot: string, p: RuntimeProvider): void => {
    api.runtime.test(slot, p.id).then((r) => setTests((t) => ({ ...t, [p.id]: r }))).catch((e: Error) => setTests((t) => ({ ...t, [p.id]: { ok: false, detail: e.message } })))
  }
  const accept = (p: RuntimeProvider, dimension: string, on: boolean): void => {
    const now = view?.accept_partial[p.id] ?? []
    act(api.runtime.acceptPartial(p.id, on ? [...now, dimension] : now.filter((d) => d !== dimension)))
  }

  const picker = (s: RuntimeSlot): JSX.Element => {
    const choices = s.providers.filter((p) => p.available)
    const label = s.providers.find((p) => p.id === s.default_id)?.label ?? s.default_id
    if (s.multi) {
      return (
        <>
          {s.providers.filter((p) => !p.core).map((p) => (
            <Switch key={p.id} title={p.label} help={p.extension ?? undefined} checked={sourceOn(s.selected[at], p.id)} disabled={!p.available}
              onChange={(on) => select(s.id, toggleSource(s.selected[at], p.id, on))} />
          ))}
          {!s.providers.some((p) => !p.core) && <p className="set-help">No extension offers a {s.noun}.</p>}
        </>
      )
    }
    return (
      <label className="setting-row"><span className="toggle-text"><b>{SCOPE_LABEL[at]}</b></span>
        <select aria-label={`${slotNoun(s.id)}, ${SCOPE_LABEL[at]}`} value={pickedId(s.selected[at])} disabled={choices.length < 2}
          onChange={(e) => select(s.id, e.target.value || null)}>
          <option value="">{at === 'global' ? `Default (${label})` : 'As set wider'}</option>
          {choices.map((p) => <option key={p.id} value={p.id}>{p.label}{p.core ? '' : ` (${p.tier})`}</option>)}
        </select>
      </label>
    )
  }

  return (
    <>
      <p className="set-help">Each seam of the app has a built-in provider, and an extension can offer another. You choose which one runs; an extension cannot choose for itself, and the permission checks, approvals and journal stay the same whichever one you pick. A chat uses its own choice first, then its project's, then a profile's, then the one set everywhere.</p>
      {scopes.length > 1 && (
        <div role="group" aria-label="Choose for">
          {scopes.map((s) => <button key={s} type="button" className={`ghost-btn sm${s === at ? ' active' : ''}`} aria-pressed={s === at} onClick={() => setScope(s)}>{SCOPE_LABEL[s]}</button>)}
        </div>
      )}
      {error && <p className="set-help" role="status">{error}</p>}
      {asked && (
        <div role="alert" className="set-row">
          <p className="small">This would loosen something:</p>
          <ul>{asked.diff.map((d) => <li key={d}>{d}</li>)}</ul>
          <button type="button" className="ghost-btn sm" onClick={() => setAsked(null)}>Cancel</button>
          <button type="button" className="ghost-btn sm danger" onClick={() => select(asked.slot, asked.value, true)}>Use it anyway</button>
        </div>
      )}
      {view?.slots.map((s) => (
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

registerSettingsPanel(CORE, { id: 'runtime', label: 'Runtime', Component: RuntimeTab })
