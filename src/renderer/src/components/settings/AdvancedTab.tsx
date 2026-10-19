import SupportSettings from '../SupportSettings'
import TraceExportSettings from '../TraceExportSettings'
import ExtensionsDev from './ExtensionsDev'
import EffectiveConfig from './EffectiveConfig'
import DataSettings from '../DataSettings'
import TrashPanel from '../TrashPanel'
import NumberField from './NumberField'
import Switch from './Switch'
import { AdvGroup, Sec, type GroupProps, type TabProps } from './shared'

export default function AdvancedTab({ draft, patch, openGroups, toggle, onExtensionsChanged }: TabProps & GroupProps & { onExtensionsChanged: () => Promise<void> }): JSX.Element {
  const gp = { openGroups, toggle }
  return (
    <section>
      <Sec id="data"><AdvGroup id="data" title="Data" {...gp}>
          <DataSettings />
          <label><span className="toggle-text"><b>Auto-archive after (days)</b><small>Chats and inbox messages with no activity for this long move to Archived. They stay searchable and can be restored. 0 turns it off.</small></span>
            <NumberField name="autoArchiveDays" value={draft.autoArchiveDays ?? 0} fallback={0} onChange={(autoArchiveDays) => patch({ autoArchiveDays })} />
          </label>
          <TrashPanel />
          <h4>Diagnostics</h4>
          <SupportSettings />
      </AdvGroup></Sec>

      <Sec id="effective"><AdvGroup id="effective" title="Effective configuration" {...gp}>
          <EffectiveConfig />
      </AdvGroup></Sec>

      <Sec id="developer"><AdvGroup id="developer" title="Developer" {...gp}>
          <Switch title="Developer tools" help="Show traces, the context preview and the system prompt." checked={draft.devTools === true} onChange={(devTools) => patch({ devTools })} />
          <TraceExportSettings value={draft.otelExport} onChange={(otelExport) => patch({ otelExport })} />
          {draft.devTools === true && <ExtensionsDev onChanged={onExtensionsChanged} />}
      </AdvGroup></Sec>
    </section>
  )
}
