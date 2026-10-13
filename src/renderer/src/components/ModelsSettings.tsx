import { useEffect, useState } from 'react'
import { ExternalLink } from '../icons'
import { api } from '../lib/api'
import { modelLabel } from '../lib/modelLabel'
import { chatRows, groupByProvider, pickerVisible, togglePicker } from '../lib/pickerModels'
import { useStore } from '../store'
import Switch from './settings/Switch'
import type { ProviderInfo } from './onboarding/steps'
import type { ModelInfo, Settings } from '@shared/types'

/** One provider's key: status, a field to save a new one, Remove. The saved key is never shown. */
function KeyRow({ p, saved }: { p: ProviderInfo; saved: boolean }): JSX.Element {
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async (apiKey: string | null): Promise<void> => {
    setBusy(true)
    setError(null)
    try {
      const r = await api.providerKeys.set(p.id, apiKey)
      useStore.setState((st) => ({ settings: { ...st.settings, providerKeysSet: r.providerKeysSet } }))
      setValue('')
      void useStore.getState().loadModels()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="setting-row key-row">
      <span className="toggle-text">
        <b>{p.name}</b>
        <small>{saved ? 'Key saved' : 'No key'}{p.keyUrl && <button type="button" className="link-btn" onClick={() => void window.open(p.keyUrl as string, '_blank')}>Get a key <ExternalLink size={11} /></button>}</small>
        {error && <small className="notice error" role="alert">{error}</small>}
      </span>
      <div className="input-row">
        <input type="password" aria-label={`${p.name} API key`} value={value} onChange={(e) => setValue(e.target.value)} placeholder={saved ? 'Replace key' : 'Paste your key'} spellCheck={false} autoComplete="off" />
        <button className="ghost-btn" type="button" disabled={busy || !value.trim()} onClick={() => void run(value.trim())}>Save</button>
        {saved && <button className="ghost-btn" type="button" disabled={busy} onClick={() => void run(null)}>Remove</button>}
      </div>
    </div>
  )
}

/** The Models tab: a key per provider (saved at once), and which models the composer picker offers (part of the Settings draft). */
export default function ModelsSettings({ settings, models, pickerModels, setPickerModels }: { settings: Settings; models: ModelInfo[]; pickerModels: string[] | undefined; setPickerModels: (ids: string[]) => void }): JSX.Element {
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [query, setQuery] = useState('')
  useEffect(() => { api.setup.providers().then((r) => setProviders(r.providers)).catch(() => undefined) }, [])

  const name = (id: string): string => providers.find((p) => p.id === id)?.name ?? id
  const shown = new Set(pickerVisible(models, pickerModels).map((m) => m.id))
  const q = query.trim().toLowerCase()
  const rows = chatRows(models).filter((m) => !q || m.id.toLowerCase().includes(q) || modelLabel(m.id).toLowerCase().includes(q))
  const groups = groupByProvider(rows, settings.provider)

  return (
    <div className="models-tab">
      <h4>API keys</h4>
      <p className="muted small">One key per provider. Keys stay on this Mac.</p>
      <div className="setting-list">
        {providers.filter((p) => p.needsKey).map((p) => <KeyRow key={p.id} p={p} saved={!!settings.providerKeysSet?.[p.id]} />)}
      </div>
      <h4 id="sec-picker">Models in the picker</h4>
      <p className="muted small">Choose which models appear in the chat box menu.</p>
      <div className="setting-row models-filter">
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter models" aria-label="Filter models" spellCheck={false} />
        <button className="ghost-btn" type="button" disabled={!pickerModels?.length} onClick={() => setPickerModels([])}>Reset to suggested</button>
      </div>
      {rows.length === 0 && <p className="muted small">No models match.</p>}
      {groups.map((g) => (
        <div key={g.provider} className="setting-list">
          {g.provider && <p className="muted small"><b>{name(g.provider)}</b></p>}
          {g.rows.map((m) => (
            <Switch key={m.id} plain={false} title={modelLabel(m.id)} label={m.id} checked={shown.has(m.id)} onChange={(on) => setPickerModels(togglePicker(models, pickerModels, m.id, on))} />
          ))}
        </div>
      ))}
    </div>
  )
}
