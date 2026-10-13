import type { Settings } from '@shared/types'
import CoworkSettings, { BrowserAccess, DeskGates } from '../CoworkSettings'
import NumberField from './NumberField'
import Switch from './Switch'
import { Sec, type TabProps } from './shared'

export default function AgentsTab({ draft, patch }: TabProps): JSX.Element {
  return (
    <section>
      <h3>Agents</h3>
      <Sec id="desks">
        <CoworkSettings draft={draft} patch={patch} />
      </Sec>
      <Sec id="workers">
        <h4>Background workers</h4>
        <Switch title="Delegate long work" help="Once a reply has used its tool rounds, the assistant hands the rest to a background worker and answers you at once." checked={draft.delegationForce !== false} onChange={(delegationForce) => patch({ delegationForce })} />
        {draft.delegationForce !== false && (
          <label className="setting-row"><span className="toggle-text"><b>Delegate after this many tool rounds</b><small>1 to 20.</small></span>
            <NumberField name="delegationAfterRounds" value={draft.delegationAfterRounds ?? 4} fallback={4} onChange={(delegationAfterRounds) => patch({ delegationAfterRounds })} />
          </label>
        )}
        <label className="setting-row"><span className="toggle-text"><b>Background workers at once</b><small>1 to 16.</small></span>
          <NumberField name="workerMaxConcurrent" value={draft.workerMaxConcurrent ?? 4} fallback={4} onChange={(workerMaxConcurrent) => patch({ workerMaxConcurrent })} />
        </label>
      </Sec>
      <Sec id="coding">
        <h4>Coding sessions</h4>
        <Switch title="Route coding to OpenCode / Claude Code" help="Grain's own agents plan, hand code changes to a coding session and check the result. They can't edit files or run file-changing commands inside a git repository themselves." checked={draft.codingRoute !== false} onChange={(codingRoute) => patch({ codingRoute })} />
        <label className="setting-row"><span className="toggle-text"><b>Default coding agent</b><small>The one Grain starts for a coding task unless you ask for another. Let Grain choose uses the first one installed.</small></span>
          <select value={draft.codingAgent ?? 'any'} onChange={(e) => patch({ codingAgent: e.target.value as Settings['codingAgent'] })}>
            <option value="any">Let Grain choose</option>
            <option value="claude">Claude Code</option>
            <option value="opencode">OpenCode</option>
            <option value="pi">pi</option>
          </select>
        </label>
        <label className="setting-row"><span className="toggle-text"><b>Coding sessions at once</b></span>
          <NumberField name="codingSessionMaxConcurrent" value={draft.codingSessionMaxConcurrent ?? 3} fallback={3} onChange={(codingSessionMaxConcurrent) => patch({ codingSessionMaxConcurrent })} />
        </label>
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
