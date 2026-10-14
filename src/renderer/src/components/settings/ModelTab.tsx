import { Fragment } from 'react'
import { useStore } from '../../store'
import { RESPONSE_STYLES, RESPONSE_STYLE_TEXT_MAX } from '../../lib/responseStyle'
import { embeddingLocked } from '../../lib/pickerModels'
import { MODEL_FIELDS } from '../../lib/providerModels'
import ProvidersSettings from '../ProvidersSettings'
import ModelsSettings from '../ModelsSettings'
import ModelSelect from '../ModelSelect'
import ModelDuels from '../ModelDuels'
import Switch from './Switch'
import { Sec, type TabProps } from './shared'
import type { Settings } from '@shared/types'

export default function ModelTab({ draft, patch, onProvidersChanged }: TabProps & { onProvidersChanged: () => Promise<void> }): JSX.Element {
  const settings = useStore((s) => s.settings)
  const models = useStore((s) => s.models)
  return (
    <section>
      <h3>Models</h3>
      <Sec id="provider"><ProvidersSettings onChanged={onProvidersChanged} /></Sec>
      <Sec id="models">
        <h4>Models</h4>
        <p className="muted small">Every model from a provider with a key. Leave a tier empty to use its provider's default.</p>
        {MODEL_FIELDS.map((f) => {
          const locked = f.key === 'embeddingModel' && embeddingLocked(settings)
          return (
            <Fragment key={f.key}>
              <label className="setting-row"><span className="toggle-text"><b>{f.label}</b>{(f.help || locked) && <small>{locked ? 'Fixed: the search index is built with it.' : f.help}</small>}</span>
                <ModelSelect kind={f.kind} label={f.label} blank={f.blank} disabled={locked} value={draft[f.key] ?? ''} onChange={(v) => patch({ [f.key]: v })} />
              </label>
              {f.key === 'workerModel' && (
                <label className="setting-row"><span className="toggle-text"><b>Worker effort</b><small>How hard workers think. Medium is faster and cheaper; Same as chat follows the chat's effort.</small></span>
                  <select value={draft.workerEffort ?? 'medium'} onChange={(e) => patch({ workerEffort: e.target.value as Settings['workerEffort'] })}>
                    <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="chat">Same as chat</option>
                  </select>
                </label>
              )}
            </Fragment>
          )
        })}
      </Sec>
      <Sec id="picker"><ModelsSettings settings={settings} models={models} pickerModels={draft.pickerModels} setPickerModels={(pickerModels) => patch({ pickerModels })} /></Sec>
      <h4>Fast replies</h4>
      <Switch title="Use the fast model for short messages" help="Short, simple messages answer without the thinking pass (or on the fast model if one is set). Long, analytical or tool-heavy messages use the chat model as usual." checked={!!draft.autoRoute} onChange={(autoRoute) => patch({ autoRoute })} />
      <Sec id="assistant">
        <h4>Assistant</h4>
        <p className="muted small">How the assistant writes and what it does on its own.</p>
        <label><span className="toggle-text"><b>Standing instructions</b></span>
          <textarea rows={6} value={draft.systemPrompt} onChange={(e) => patch({ systemPrompt: e.target.value })} />
        </label>
        <label className="setting-row"><span className="toggle-text"><b>Reply style</b></span>
          <select value={draft.responseStyle ?? 'default'} onChange={(e) => patch({ responseStyle: e.target.value })}>
            {RESPONSE_STYLES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </label>
        {draft.responseStyle === 'custom' && <label><span className="toggle-text"><small>Your own instruction for how replies should read.</small></span>
          <textarea rows={4} maxLength={RESPONSE_STYLE_TEXT_MAX} value={draft.responseStyleText ?? ''} onChange={(e) => patch({ responseStyleText: e.target.value })} />
        </label>}
        <Switch title="Name new chats" help="Gives each new chat a short title after the first reply." checked={draft.autoTitle !== false} onChange={(autoTitle) => patch({ autoTitle })} />
        <Switch title="Suggest next questions" help="Shows follow-up ideas under each reply." checked={draft.followUps !== false} onChange={(followUps) => patch({ followUps })} />
        <Switch title="Selection toolbar" help="Shows quick actions when you select text." checked={draft.selectionToolbar !== false} onChange={(selectionToolbar) => patch({ selectionToolbar })} />
        <Switch title="Summarize old messages automatically" help="When a chat gets long. Off: only when you ask (type /compact)." checked={draft.autoCompact !== false} onChange={(autoCompact) => patch({ autoCompact })} />
      </Sec>
      {draft.devTools === true && <Sec id="duels"><ModelDuels /></Sec>}
    </section>
  )
}
