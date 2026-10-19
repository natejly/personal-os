import type { ReactNode } from 'react'
import type { Settings } from '@shared/types'
import { slotNoun, sourceText } from '../../lib/runtime'
import NumberField, { RANGES } from './NumberField'
import SettingRow from './SettingRow'
import { ScopeButtons, SelectNotices, SlotPicker } from './RuntimeParts'
import { Sec, type TabProps } from './shared'
import { useRuntimeSelect } from './useRuntimeSelect'

export const LOOP_SLOT = 'agent.loop'

export type StuckMode = NonNullable<Settings['stuckDetection']>
export const STUCK_MODES: { id: StuckMode; text: string }[] = [
  { id: 'off', text: 'Never stops a reply for repeating itself.' },
  { id: 'relaxed', text: 'Stops only clear loops.' },
  { id: 'standard', text: 'Stops repeated calls and repeated errors.' },
  { id: 'strict', text: 'Stops sooner, on weaker signs of a loop.' }
]

type NumKey = 'loopMaxRounds' | 'loopModelCalls' | 'loopRepeatLimit' | 'loopToolErrorLimit' | 'loopDoomLimit' | 'finalRoundSeconds'
  | 'toolResultInlineChars' | 'planLoopMaxSteps' | 'planLoopVerifyPasses' | 'parallelReads' | 'llmRetries' | 'llmIdleSeconds'

/** Built-in defaults, as the backend ships them. A field the Settings object leaves out reads as these. */
export const LOOP_DEFAULTS: Record<NumKey, number> & { stuckDetection: StuckMode } = {
  loopMaxRounds: 0, loopModelCalls: 60, loopRepeatLimit: 5, loopToolErrorLimit: 3, loopDoomLimit: 3, stuckDetection: 'standard',
  finalRoundSeconds: 90, toolResultInlineChars: 12000, planLoopMaxSteps: 8, planLoopVerifyPasses: 1,
  parallelReads: 0, llmRetries: 3, llmIdleSeconds: 300
}

/** "Range 5 to 500." for a numeric key, with a note for the special 0. */
export const rangeText = (key: NumKey, zero?: string): string => {
  const [lo, hi] = RANGES[key]
  return `Range ${lo} to ${hi}${zero ? `; 0 means ${zero}` : ''}.`
}

const NUMBERS: { key: NumKey; title: string; help: string; zero?: string; group: string }[] = [
  { group: 'Limits', key: 'loopMaxRounds', title: 'Most rounds per reply', help: 'Tool rounds a reply may take before it is stopped.', zero: 'no cap' },
  { group: 'Limits', key: 'loopModelCalls', title: 'Model calls per turn', help: 'Applies to an alternative agent loop; the chat loop has no cap.' },
  { group: 'Limits', key: 'finalRoundSeconds', title: 'Closing answer timeout (seconds)', help: 'A closing answer still open after this long is abandoned.' },
  { group: 'Limits', key: 'toolResultInlineChars', title: 'Tool result size kept inline (characters)', help: 'A longer result is stored and replaced by a preview.' },
  { group: 'Stuck detection', key: 'loopRepeatLimit', title: 'Identical calls in a row', help: 'Same tool call repeated this many times stops the reply.' },
  { group: 'Stuck detection', key: 'loopToolErrorLimit', title: 'Errors in a row from one tool', help: 'After this many, the model is told to change approach.' },
  { group: 'Stuck detection', key: 'loopDoomLimit', title: 'Repeated dead ends', help: 'Stops a reply that keeps returning to the same failing step.' },
  { group: 'Plan loop', key: 'planLoopMaxSteps', title: 'Most plan steps', help: 'Steps the plan, execute, verify loop may plan.' },
  { group: 'Plan loop', key: 'planLoopVerifyPasses', title: 'Verify passes', help: 'Times the result is checked and fixed.', zero: 'no check' },
  { group: 'Calls', key: 'parallelReads', title: 'Parallel read-only calls', help: 'Read-only calls of one round that run together.', zero: 'automatic' },
  { group: 'Calls', key: 'llmRetries', title: 'Retries before the first token', help: 'How often a failed model call is tried again.' },
  { group: 'Calls', key: 'llmIdleSeconds', title: 'Silent stream timeout (seconds)', help: 'A model stream with no output this long is abandoned.' }
]

/** The Agent section's Loop group: which loop runs, and the limits that end or guard a reply. */
export default function LoopGroup({ draft, patch }: TabProps): JSX.Element {
  const rt = useRuntimeSelect()
  const slot = rt.view?.slots.find((s) => s.id === LOOP_SLOT)
  const stuck = draft.stuckDetection ?? LOOP_DEFAULTS.stuckDetection
  const row = (n: (typeof NUMBERS)[number]): ReactNode => {
    const value = draft[n.key] ?? LOOP_DEFAULTS[n.key]
    return (
      <SettingRow key={n.key} settingKey={n.key} title={n.title} help={`${n.help} ${rangeText(n.key, n.zero)}`} value={value} defaultValue={LOOP_DEFAULTS[n.key]}
        onReset={() => patch({ [n.key]: LOOP_DEFAULTS[n.key] })}>
        <NumberField name={n.key} label={n.title} value={value} fallback={LOOP_DEFAULTS[n.key]} onChange={(v) => patch({ [n.key]: v })} />
      </SettingRow>
    )
  }
  const sections = ['Limits', 'Stuck detection', 'Plan loop', 'Calls']
  return (
    <Sec id="loop">
      <h4>Loop</h4>
      <p className="set-help">How a reply runs: which loop drives it, when it is stopped, and how model calls are retried.</p>
      <div data-testid={`runtime-${LOOP_SLOT}`}>
        <h5 className="set-subhead">{slotNoun(LOOP_SLOT)}</h5>
        {slot && (
          <p className="set-help" data-testid={`runtime-${LOOP_SLOT}-reason`}>{slot.resolution.reason} <small>({sourceText(slot.resolution.source)})</small></p>
        )}
        <ScopeButtons rt={rt} />
        <SelectNotices rt={rt} />
        {slot && <SlotPicker slot={slot} rt={rt} />}
      </div>
      {sections.map((g) => (
        <div key={g}>
          <h5 className="set-subhead">{g}</h5>
          {g === 'Stuck detection' && (
            <SettingRow settingKey="stuckDetection" title="Stuck detection" help={STUCK_MODES.find((m) => m.id === stuck)?.text} value={stuck} defaultValue={LOOP_DEFAULTS.stuckDetection}
              onReset={() => patch({ stuckDetection: LOOP_DEFAULTS.stuckDetection })}>
              <select aria-label="Stuck detection" value={stuck} onChange={(e) => patch({ stuckDetection: e.target.value as StuckMode })}>
                {STUCK_MODES.map((m) => <option key={m.id} value={m.id}>{m.id[0].toUpperCase() + m.id.slice(1)}: {m.text}</option>)}
              </select>
            </SettingRow>
          )}
          {NUMBERS.filter((n) => n.group === g).map(row)}
        </div>
      ))}
    </Sec>
  )
}
