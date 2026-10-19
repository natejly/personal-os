import { useState, type ReactNode } from 'react'
import type { PermissionPreset, PresetBody } from '@shared/types'
import { api } from '../../lib/api'
import { modeOf } from '../../lib/permissionMode'
import { useStore } from '../../store'
import { PermissionModeCards } from '../PermissionMode'
import { DEFAULT_DESTRUCTIVE, DestructiveDefaults } from '../DestructiveDefaults'
import PermissionRules from '../PermissionRules'
import { AlwaysAsk, ToolGlobalToggles } from '../ToolPermissions'

/** A part of a preset: left out unless included, so a preset changes only what it names. */
function Part({ title, on, set, children }: { title: string; on: boolean; set: (on: boolean) => void; children: ReactNode }): JSX.Element {
  return (
    <div className="preset-part">
      <label className="preset-include"><input type="checkbox" checked={on} onChange={(e) => set(e.target.checked)} /> <b>{title}</b></label>
      {on && children}
    </div>
  )
}

/** Create or edit one of your presets with the same controls Settings uses for the live values. */
export default function PresetEditor({ preset, onDone }: { preset: PermissionPreset | null; onDone: (saved: boolean) => void }): JSX.Element {
  const [body, setBody] = useState<PresetBody>(preset?.body ?? { name: '' })
  const toast = useStore((s) => s.toast)
  const patch = (p: Partial<PresetBody>): void => setBody((b) => ({ ...b, ...p }))
  const drop = (key: keyof PresetBody): void => setBody((b) => { const { [key]: _gone, ...rest } = b; return rest as PresetBody })
  const part = (key: keyof PresetBody, initial: Partial<PresetBody>) => ({ on: key in body, set: (on: boolean) => (on ? patch(initial) : drop(key)) })
  const save = (): void => {
    const saved = preset ? api.presets.update(preset.id, body) : api.presets.create(body)
    saved.then(() => onDone(true)).catch((e: Error) => toast(e.message, 'error'))
  }
  return (
    <div className="preset-editor" role="group" aria-label={preset ? `Edit ${preset.name}` : 'New preset'}>
      <label className="setting-row"><span className="toggle-text"><b>Name</b></span>
        <input value={body.name} onChange={(e) => patch({ name: e.target.value })} aria-label="Preset name" spellCheck={false} />
      </label>
      <label className="setting-row"><span className="toggle-text"><b>Description</b></span>
        <input value={body.description ?? ''} onChange={(e) => patch({ description: e.target.value })} aria-label="Preset description" />
      </label>
      <Part title="Permission mode" {...part('permissionMode', { permissionMode: 'manual' })}>
        <PermissionModeCards mode={modeOf(body)} onPick={(permissionMode) => patch({ permissionMode })} />
      </Part>
      <Part title="Destructive actions (Manual mode)" {...part('destructiveDefaults', { destructiveDefaults: { ...DEFAULT_DESTRUCTIVE } })}>
        <DestructiveDefaults value={body.destructiveDefaults} onChange={(destructiveDefaults) => patch({ destructiveDefaults })} />
      </Part>
      <Part title="Permission rules" {...part('permissionRules', { permissionRules: { allow: [], ask: [], deny: [] } })}>
        <PermissionRules value={body.permissionRules} onChange={(permissionRules) => patch({ permissionRules })} />
      </Part>
      <Part title="Always ask first" {...part('alwaysAsk', { alwaysAsk: [] })}>
        <AlwaysAsk value={body.alwaysAsk ?? []} onChange={(alwaysAsk) => patch({ alwaysAsk })} />
      </Part>
      <Part title="Per-tool access" {...part('tools', { tools: {} })}>
        <ToolGlobalToggles value={body.tools?.tools ?? {}} onChange={(tools) => patch({ tools: { ...body.tools, tools } })} />
      </Part>
      <div className="preset-actions">
        <button className="ghost-btn" onClick={() => onDone(false)}>Cancel</button>
        <button className="ghost-btn" disabled={!body.name.trim()} onClick={save}>Save preset</button>
      </div>
    </div>
  )
}
