import { RotateCcw } from '../../icons'
import { useStore } from '../../store'
import { RESPONSE_STYLES, RESPONSE_STYLE_TEXT_MAX } from '../../lib/responseStyle'
import ProviderSettings from '../ProviderSettings'
import Switch from './Switch'
import { Sec, type TabProps } from './shared'

export default function ModelTab({ draft, patch, onRunSetup }: TabProps & { onRunSetup: () => void }): JSX.Element {
  const settings = useStore((s) => s.settings)
  const models = useStore((s) => s.models)
  return (
    <section>
      <h3>Model</h3>
      <Sec id="provider"><ProviderSettings draft={draft} settings={settings} patch={patch} models={models} /></Sec>
      <Switch title="Use the fast model for short messages" help="Short, simple messages answer without the thinking pass (or on the fast model if one is set). Long, analytical or tool-heavy messages use the chat model as usual." checked={!!draft.autoRoute} onChange={(autoRoute) => patch({ autoRoute })} />
      <div className="setting-row">
        <span className="toggle-text"><b>Run setup again</b></span>
        <button className="ghost-btn" type="button" onClick={onRunSetup}><RotateCcw size={14} /> Run setup</button>
      </div>
      <Sec id="assistant">
        <h4>Assistant</h4>
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
        <Switch title="Name new chats" checked={draft.autoTitle !== false} onChange={(autoTitle) => patch({ autoTitle })} />
        <Switch title="Suggest next questions" checked={draft.followUps !== false} onChange={(followUps) => patch({ followUps })} />
        <Switch title="Selection toolbar" checked={draft.selectionToolbar !== false} onChange={(selectionToolbar) => patch({ selectionToolbar })} />
        <Switch title="Summarize old messages automatically" help="When a chat gets long. Off: only when you ask (type /compact)." checked={draft.autoCompact !== false} onChange={(autoCompact) => patch({ autoCompact })} />
      </Sec>
    </section>
  )
}
