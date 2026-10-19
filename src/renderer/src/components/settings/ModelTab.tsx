import { Fragment } from 'react'
import { useStore } from '../../store'
import { RESPONSE_STYLES, RESPONSE_STYLE_TEXT_MAX } from '../../lib/responseStyle'
import { embeddingLocked } from '../../lib/pickerModels'
import { MODEL_FIELDS } from '../../lib/providerModels'
import ProvidersSettings from '../ProvidersSettings'
import ModelsSettings from '../ModelsSettings'
import ModelSelect from '../ModelSelect'
import ModelDuels from '../ModelDuels'
import SettingRow from './SettingRow'
import { Sec, type TabProps } from './shared'
import type { Settings } from '@shared/types'

const DEFAULT_MODEL_VALUE = ''

/** A bare on/off control for a SettingRow (the row supplies label and help). */
function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }): JSX.Element {
  return <label className="switch-wrap"><input type="checkbox" aria-label={label} checked={checked} onChange={(e) => onChange(e.target.checked)} /><span className="switch" /></label>
}

export default function ModelTab({ draft, patch, onProvidersChanged }: TabProps & { onProvidersChanged: () => Promise<void> }): JSX.Element {
  const settings = useStore((s) => s.settings)
  const models = useStore((s) => s.models)
  return (
    <section>
      <Sec id="provider"><ProvidersSettings onChanged={onProvidersChanged} /></Sec>
      <Sec id="models">
        <h4>Models</h4>
        <p className="muted small">Every model from a provider with a key. Leave a tier empty to use its provider's default.</p>
        {MODEL_FIELDS.map((f) => {
          const locked = f.key === 'embeddingModel' && embeddingLocked(settings)
          const value = draft[f.key] ?? ''
          return (
            <Fragment key={f.key}>
              <SettingRow settingKey={f.key} title={f.label} help={locked ? 'Fixed: the search index is built with it.' : f.help || undefined}
                value={value} defaultValue={DEFAULT_MODEL_VALUE} onReset={locked ? undefined : () => patch({ [f.key]: DEFAULT_MODEL_VALUE })}>
                <ModelSelect kind={f.kind} label={f.label} blank={f.blank} disabled={locked} value={value} onChange={(v) => patch({ [f.key]: v })} />
              </SettingRow>
              {f.key === 'workerModel' && (
                <SettingRow settingKey="workerEffort" title="Worker effort" help="How hard workers think. Medium is faster and cheaper; Same as chat follows the chat's effort."
                  value={draft.workerEffort ?? 'medium'} defaultValue="medium" onReset={() => patch({ workerEffort: 'medium' })}>
                  <select aria-label="Worker effort" value={draft.workerEffort ?? 'medium'} onChange={(e) => patch({ workerEffort: e.target.value as Settings['workerEffort'] })}>
                    <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="chat">Same as chat</option>
                  </select>
                </SettingRow>
              )}
            </Fragment>
          )
        })}
      </Sec>
      <Sec id="picker"><ModelsSettings settings={settings} models={models} pickerModels={draft.pickerModels} setPickerModels={(pickerModels) => patch({ pickerModels })} /></Sec>
      <h4>Fast replies</h4>
      <p className="muted small">Skip the thinking pass for simple messages.</p>
      <SettingRow settingKey="autoRoute" title="Use the fast model for short messages" help="Short, simple messages answer without the thinking pass (or on the fast model if one is set). Long, analytical or tool-heavy messages use the chat model as usual."
        value={!!draft.autoRoute} defaultValue={false} onReset={() => patch({ autoRoute: false })}>
        <Toggle label="Use the fast model for short messages" checked={!!draft.autoRoute} onChange={(autoRoute) => patch({ autoRoute })} />
      </SettingRow>
      <Sec id="assistant">
        <h4>Assistant</h4>
        <p className="muted small">How the assistant writes and what it does on its own.</p>
        <SettingRow settingKey="systemPrompt" title="Standing instructions" help="Sent with every chat, ahead of your messages.">
          <textarea rows={6} aria-label="Standing instructions" value={draft.systemPrompt} onChange={(e) => patch({ systemPrompt: e.target.value })} />
        </SettingRow>
        <SettingRow settingKey="responseStyle" title="Reply style" help="The tone and length a new chat starts with."
          value={draft.responseStyle ?? 'default'} defaultValue="default" onReset={() => patch({ responseStyle: 'default' })}>
          <select aria-label="Reply style" value={draft.responseStyle ?? 'default'} onChange={(e) => patch({ responseStyle: e.target.value })}>
            {RESPONSE_STYLES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </SettingRow>
        {draft.responseStyle === 'custom' && <SettingRow settingKey="responseStyleText" title="Custom style" help="Your own instruction for how replies should read."
          value={draft.responseStyleText ?? ''} defaultValue="" onReset={() => patch({ responseStyleText: '' })}>
          <textarea rows={4} aria-label="Custom style" maxLength={RESPONSE_STYLE_TEXT_MAX} value={draft.responseStyleText ?? ''} onChange={(e) => patch({ responseStyleText: e.target.value })} />
        </SettingRow>}
        <SettingRow settingKey="autoTitle" title="Name new chats" help="Gives each new chat a short title after the first reply."
          value={draft.autoTitle !== false} defaultValue={true} onReset={() => patch({ autoTitle: true })}>
          <Toggle label="Name new chats" checked={draft.autoTitle !== false} onChange={(autoTitle) => patch({ autoTitle })} />
        </SettingRow>
        <SettingRow settingKey="followUps" title="Suggest next questions" help="Shows follow-up ideas under each reply."
          value={draft.followUps !== false} defaultValue={true} onReset={() => patch({ followUps: true })}>
          <Toggle label="Suggest next questions" checked={draft.followUps !== false} onChange={(followUps) => patch({ followUps })} />
        </SettingRow>
        <SettingRow settingKey="selectionToolbar" title="Selection toolbar" help="Shows quick actions when you select text."
          value={draft.selectionToolbar !== false} defaultValue={true} onReset={() => patch({ selectionToolbar: true })}>
          <Toggle label="Selection toolbar" checked={draft.selectionToolbar !== false} onChange={(selectionToolbar) => patch({ selectionToolbar })} />
        </SettingRow>
        <SettingRow settingKey="autoCompact" title="Summarize old messages automatically" help="When a chat gets long. Off: only when you ask (type /compact)."
          value={draft.autoCompact !== false} defaultValue={true} onReset={() => patch({ autoCompact: true })}>
          <Toggle label="Summarize old messages automatically" checked={draft.autoCompact !== false} onChange={(autoCompact) => patch({ autoCompact })} />
        </SettingRow>
      </Sec>
      {draft.devTools === true && <Sec id="duels"><ModelDuels /></Sec>}
    </section>
  )
}
