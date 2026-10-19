import { useState } from 'react'
import { modelLabel } from '../lib/modelLabel'
import { chatRows, groupByProvider, pickerVisible, togglePicker } from '../lib/pickerModels'
import Switch from './settings/Switch'
import type { ModelInfo, Settings } from '@shared/types'

/** Which models the composer picker offers (part of the Settings draft). */
export default function ModelsSettings({ settings, models, pickerModels, setPickerModels }: { settings: Settings; models: ModelInfo[]; pickerModels: string[] | undefined; setPickerModels: (ids: string[]) => void }): JSX.Element {
  const [query, setQuery] = useState('')
  const name = (id: string): string => settings.providers?.find((p) => p.id === id)?.name ?? id
  const shown = new Set(pickerVisible(models, pickerModels).map((m) => m.id))
  const q = query.trim().toLowerCase()
  const rows = chatRows(models).filter((m) => !q || m.id.toLowerCase().includes(q) || modelLabel(m.id).toLowerCase().includes(q))
  const groups = groupByProvider(rows)

  return (
    <div className="models-tab">
      <h4>Models in the picker</h4>
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
