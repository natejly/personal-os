import { sourceText } from '../../lib/settingSource'
import type { ReactNode } from 'react'
import type { Settings } from '@shared/types'
import { slotNoun } from '../../lib/runtime'
import NumberField from './NumberField'
import SettingRow from './SettingRow'
import { ScopeButtons, SelectNotices, SlotPicker } from './RuntimeParts'
import { Sec, type TabProps } from './shared'
import { useRuntimeSelect } from './useRuntimeSelect'

export const LOOP_SLOT = 'agent.loop'

export type StuckMode = NonNullable<Settings['stuckSensitivity']>
export const STUCK_MODES: { id: StuckMode; label: string }[] = [
  { id: 'off', label: 'Off' },
  { id: 'relaxed', label: 'Relaxed' },
  { id: 'standard', label: 'Standard' },
  { id: 'strict', label: 'Strict' }
]

type NumKey = 'loopMaxRounds' | 'loopModelCalls' | 'loopRepeatLimit' | 'loopToolErrorLimit' | 'loopDoomLimit' | 'finalRoundSeconds'
  | 'toolResultInlineChars' | 'planLoopMaxSteps' | 'planLoopVerifyPasses' | 'parallelReads' | 'llmRetries' | 'llmIdleSeconds'

/** Built-in defaults, as the backend ships them. A field the Settings object leaves out reads as these. */
export const LOOP_DEFAULTS: Record<NumKey, number> & { stuckSensitivity: StuckMode } = {
  loopMaxRounds: 0, loopModelCalls: 60, loopRepeatLimit: 5, loopToolErrorLimit: 3, loopDoomLimit: 3, stuckSensitivity: 'standard',
  finalRoundSeconds: 90, toolResultInlineChars: 12000, planLoopMaxSteps: 8, planLoopVerifyPasses: 1,
  parallelReads: 0, llmRetries: 3, llmIdleSeconds: 300
}

const NUMBERS: { key: NumKey; title: string; group: string }[] = [
  { group: 'Limits', key: 'loopMaxRounds', title: 'Most rounds per reply (0 = no cap)' },
  { group: 'Limits', key: 'loopModelCalls', title: 'Model calls per turn' },
  { group: 'Limits', key: 'finalRoundSeconds', title: 'Closing answer timeout (seconds)' },
  { group: 'Limits', key: 'toolResultInlineChars', title: 'Tool result size kept inline (characters)' },
  { group: 'Stuck detection', key: 'loopRepeatLimit', title: 'Identical calls in a row' },
  { group: 'Stuck detection', key: 'loopToolErrorLimit', title: 'Errors in a row from one tool' },
  { group: 'Stuck detection', key: 'loopDoomLimit', title: 'Repeated dead ends' },
  { group: 'Plan loop', key: 'planLoopMaxSteps', title: 'Most plan steps' },
  { group: 'Plan loop', key: 'planLoopVerifyPasses', title: 'Verify passes (0 = no check)' },
  { group: 'Calls', key: 'parallelReads', title: 'Parallel read-only calls (0 = automatic)' },
  { group: 'Calls', key: 'llmRetries', title: 'Retries before the first token' },
  { group: 'Calls', key: 'llmIdleSeconds', title: 'Silent stream timeout (seconds)' }
]

/** The Agent section's Loop group: which loop runs, and the limits that end or guard a reply. */
export default function LoopGroup({ draft, patch }: TabProps): JSX.Element {
  const rt = useRuntimeSelect()
  const slot = rt.view?.slots.find((s) => s.id === LOOP_SLOT)
  const stuck = draft.stuckSensitivity ?? LOOP_DEFAULTS.stuckSensitivity
  const row = (n: (typeof NUMBERS)[number]): ReactNode => {
    const value = draft[n.key] ?? LOOP_DEFAULTS[n.key]
    return (
      <SettingRow key={n.key} settingKey={n.key} title={n.title} value={value} defaultValue={LOOP_DEFAULTS[n.key]}
        onReset={() => patch({ [n.key]: LOOP_DEFAULTS[n.key] })}>
        <NumberField name={n.key} label={n.title} value={value} fallback={LOOP_DEFAULTS[n.key]} onChange={(v) => patch({ [n.key]: v })} />
      </SettingRow>
    )
  }
  const sections = ['Limits', 'Stuck detection', 'Plan loop', 'Calls']
  return (
    <Sec id="loop">
      <h4>Loop</h4>
      <div data-testid={`runtime-${LOOP_SLOT}`}>
        <h5 className="set-subhead">{slotNoun(LOOP_SLOT)}</h5>
        <ScopeButtons rt={rt} />
        <SelectNotices rt={rt} />
        {slot && <small data-testid={`runtime-${LOOP_SLOT}-reason`}>{sourceText(slot.resolution.source)}</small>}
        {slot && <SlotPicker slot={slot} rt={rt} />}
      </div>
      {sections.map((g) => (
        <div key={g}>
          <h5 className="set-subhead">{g}</h5>
          {g === 'Stuck detection' && (
            <SettingRow settingKey="stuckSensitivity" title="Stuck detection" value={stuck} defaultValue={LOOP_DEFAULTS.stuckSensitivity}
              onReset={() => patch({ stuckSensitivity: LOOP_DEFAULTS.stuckSensitivity })}>
              <select aria-label="Stuck detection" value={stuck} onChange={(e) => patch({ stuckSensitivity: e.target.value as StuckMode })}>
                {STUCK_MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
              </select>
            </SettingRow>
          )}
          {NUMBERS.filter((n) => n.group === g).map(row)}
        </div>
      ))}
    </Sec>
  )
}
