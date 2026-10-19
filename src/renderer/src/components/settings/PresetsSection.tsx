import { useEffect, useRef, useState } from 'react'
import type { PermissionPreset, PresetDiff, PresetScope } from '@shared/types'
import { api } from '../../lib/api'
import { diffRows, presetOrigin, SCOPE_LABEL, scopeId, scopesFor } from '../../lib/presets'
import { useStore } from '../../store'
import PresetEditor from './PresetEditor'
import { Sec } from './shared'

/** A preset's diff, the changes that allow more first. Nothing has been applied while it is on screen. */
function Diff({ diff }: { diff: PresetDiff }): JSX.Element {
  return (
    <table className="preset-diff">
      <thead><tr><th>Setting</th><th>Now</th><th>After</th></tr></thead>
      <tbody>
        {diffRows(diff).map((r) => (
          <tr key={r.key} className={r.loosens ? 'loosens' : ''}><td>{r.label}{r.loosens && <small> allows more</small>}</td><td>{r.old}</td><td>{r.next}</td></tr>
        ))}
      </tbody>
    </table>
  )
}

/**
 * Permission presets: named bundles of permission settings. Applying is an action, like a profile's switch, and not part of the Save
 * draft. A preset that allows more than is in force at the scope shows its diff and applies only on the second click.
 */
export default function PresetsSection({ onApplied }: { onApplied: () => Promise<void> }): JSX.Element {
  const [presets, setPresets] = useState<PermissionPreset[]>([])
  const [scope, setScope] = useState<PresetScope>('global')
  const [held, setHeld] = useState<{ preset: PermissionPreset; diff: PresetDiff } | null>(null)
  const [editing, setEditing] = useState<PermissionPreset | 'new' | null>(null)
  const file = useRef<HTMLInputElement>(null)
  const toast = useStore((s) => s.toast)
  const chatId = useStore((s) => s.focusedConversationId)
  const projectId = useStore((s) => s.conversations.find((c) => c.id === s.focusedConversationId)?.project_id ?? s.projectViewId)
  const scopes = scopesFor(projectId, chatId)
  const where = scopes.includes(scope) ? scope : 'global'
  const reload = (): void => { api.presets.list().then(setPresets).catch(() => undefined) }
  useEffect(reload, [])
  const fail = (e: Error): void => toast(e.message, 'error')

  const apply = async (preset: PermissionPreset, confirm?: string): Promise<void> => {
    const r = await api.presets.apply(preset.id, where, scopeId(where, projectId, chatId), confirm)
    if ('held' in r) { setHeld({ preset, diff: r.held }); return }
    setHeld(null)
    const n = Object.keys(r.applied.changes).length
    toast(n ? `${preset.name} applied: ${n} setting${n === 1 ? '' : 's'} changed${r.applied.not_applied.length ? `; ${r.applied.not_applied.join(', ')} apply everywhere only` : ''}` : `${preset.name}: nothing to change`, 'info')
    await onApplied()
  }
  const exportPreset = async (p: PermissionPreset): Promise<void> => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(await api.presets.export(p.id), null, 2)], { type: 'application/json' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `${p.id.replace(/[^a-z0-9-]+/g, '-')}.json`
    a.click()
    URL.revokeObjectURL(url)
  }
  const importFile = async (f: File): Promise<void> => {
    await api.presets.import(JSON.parse(await f.text()))
    reload()
  }

  return (
    <Sec id="presets">
      <h4>Presets</h4>
      <div className="preset-scope" role="group" aria-label="Apply to">
        {scopes.map((s) => <button key={s} type="button" className={`ghost-btn ${where === s ? 'on' : ''}`} aria-pressed={where === s} onClick={() => { setScope(s); setHeld(null) }}>{SCOPE_LABEL[s]}</button>)}
      </div>
      <ul className="preset-list">
        {presets.map((p) => (
          <li key={p.id} data-preset={p.id}>
            <span className="toggle-text"><b>{p.name}</b><small>{presetOrigin(p)}</small></span>
            <span className="preset-buttons">
              <button className="ghost-btn" aria-label={`Apply ${p.name}`} onClick={() => apply(p).catch(fail)}>Apply</button>
              <button className="ghost-btn" aria-label={`Duplicate ${p.name}`} onClick={() => api.presets.duplicate(p.id).then(reload).catch(fail)}>Duplicate</button>
              <button className="ghost-btn" aria-label={`Export ${p.name}`} onClick={() => exportPreset(p).catch(fail)}>Export</button>
              {!p.readonly && <button className="ghost-btn" aria-label={`Edit ${p.name}`} onClick={() => setEditing(p)}>Edit</button>}
              {!p.readonly && <button className="ghost-btn danger" aria-label={`Delete ${p.name}`} onClick={() => api.presets.remove(p.id).then(reload).catch(fail)}>Delete</button>}
            </span>
          </li>
        ))}
      </ul>
      <div className="preset-buttons">
        <button className="ghost-btn" onClick={() => setEditing('new')}>New preset</button>
        <button className="ghost-btn" onClick={() => file.current?.click()}>Import</button>
        <input ref={file} type="file" accept="application/json,.json" hidden aria-label="Import a preset" onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) importFile(f).catch(fail) }} />
      </div>
      {held && (
        <div role="alert" className="preset-held">
          <p className="small"><b>{held.preset.name}</b> would allow more than {SCOPE_LABEL[where].toLowerCase()} has now. Nothing has been applied.</p>
          <Diff diff={held.diff} />
          {held.diff.not_applied.length > 0 && <p className="muted small">Not applied here, everywhere only: {held.diff.not_applied.join(', ')}.</p>}
          <button className="ghost-btn" onClick={() => setHeld(null)}>Cancel</button>
          <button className="ghost-btn danger" onClick={() => apply(held.preset, held.diff.diff_hash).catch(fail)}>Apply anyway</button>
        </div>
      )}
      {editing && <PresetEditor preset={editing === 'new' ? null : editing} onDone={(saved) => { setEditing(null); if (saved) reload() }} />}
    </Sec>
  )
}
