import type { Settings } from '@shared/types'
import CoworkSettings, { BrowserAccess, DeskGates } from '../CoworkSettings'
import LoopGroup from './LoopGroup'
import NumberField from './NumberField'
import SettingRow from './SettingRow'
import { Sec, type TabProps } from './shared'

/** A bare on/off control for a SettingRow (the row supplies label and help). */
function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }): JSX.Element {
  return <label className="switch-wrap"><input type="checkbox" aria-label={label} checked={checked} onChange={(e) => onChange(e.target.checked)} /><span className="switch" /></label>
}

export default function AgentsTab({ draft, patch }: TabProps): JSX.Element {
  const delegationAfterRounds = draft.delegationAfterRounds ?? 4
  const workerMaxConcurrent = draft.workerMaxConcurrent ?? 4
  const codingSessionMaxConcurrent = draft.codingSessionMaxConcurrent ?? 3
  const codingAgent = draft.codingAgent ?? 'any'
  return (
    <section>
      <LoopGroup draft={draft} patch={patch} />
      <Sec id="desks">
        <CoworkSettings draft={draft} patch={patch} />
      </Sec>
      <Sec id="workers">
        <h4>Background workers</h4>
        <SettingRow title="Delegate long work" help="Once a reply has used its tool rounds, the assistant hands the rest to a background worker and answers you at once."
          value={draft.delegationForce !== false} defaultValue onReset={() => patch({ delegationForce: true })}>
          <Toggle label="Delegate long work" checked={draft.delegationForce !== false} onChange={(delegationForce) => patch({ delegationForce })} />
        </SettingRow>
        {draft.delegationForce !== false && (
          <SettingRow title="Delegate after this many tool rounds" help="1 to 20." value={delegationAfterRounds} defaultValue={4} onReset={() => patch({ delegationAfterRounds: 4 })}>
            <NumberField name="delegationAfterRounds" label="Delegate after this many tool rounds" value={delegationAfterRounds} fallback={4} onChange={(n) => patch({ delegationAfterRounds: n })} />
          </SettingRow>
        )}
        <SettingRow title="Background workers at once" help="1 to 16." value={workerMaxConcurrent} defaultValue={4} onReset={() => patch({ workerMaxConcurrent: 4 })}>
          <NumberField name="workerMaxConcurrent" label="Background workers at once" value={workerMaxConcurrent} fallback={4} onChange={(n) => patch({ workerMaxConcurrent: n })} />
        </SettingRow>
      </Sec>
      <Sec id="coding">
        <h4>Coding sessions</h4>
        <SettingRow title="Route coding to OpenCode / Claude Code" help="Grain's own agents plan, hand code changes to a coding session and check the result. They can't edit files or run file-changing commands inside a git repository themselves."
          value={draft.codingRoute !== false} defaultValue onReset={() => patch({ codingRoute: true })}>
          <Toggle label="Route coding to OpenCode / Claude Code" checked={draft.codingRoute !== false} onChange={(codingRoute) => patch({ codingRoute })} />
        </SettingRow>
        <SettingRow title="Default coding agent" help="The one Grain starts for a coding task unless you ask for another. Let Grain choose uses the first one installed."
          value={codingAgent} defaultValue="any" onReset={() => patch({ codingAgent: 'any' })}>
          <select aria-label="Default coding agent" value={codingAgent} onChange={(e) => patch({ codingAgent: e.target.value as Settings['codingAgent'] })}>
            <option value="any">Let Grain choose</option>
            <option value="claude">Claude Code</option>
            <option value="opencode">OpenCode</option>
            <option value="pi">pi</option>
          </select>
        </SettingRow>
        <SettingRow title="Coding sessions at once" help="1 to 20." value={codingSessionMaxConcurrent} defaultValue={3} onReset={() => patch({ codingSessionMaxConcurrent: 3 })}>
          <NumberField name="codingSessionMaxConcurrent" label="Coding sessions at once" value={codingSessionMaxConcurrent} fallback={3} onChange={(n) => patch({ codingSessionMaxConcurrent: n })} />
        </SettingRow>
      </Sec>
      <Sec id="browser">
        <h4>Browser</h4>
        <BrowserAccess draft={draft} patch={patch} />
      </Sec>
      <Sec id="checks">
        <h4>Checks and commands</h4>
        <DeskGates draft={draft} patch={patch} />
      </Sec>
    </section>
  )
}
