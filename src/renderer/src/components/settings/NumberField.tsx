import { clampNumber } from '../../lib/clampNumber'

/** Accepted ranges of the numeric settings shown here; mirrors backend limits.RANGES (not served over the API). */
export const RANGES = {
  delegationAfterRounds: [1, 20],
  workerMaxConcurrent: [1, 16],
  codingSessionMaxConcurrent: [1, 20],
  autoArchiveDays: [0, 3650], // 0 = off; the backend takes 0 as "automatic" and 1 to 3650
  planAutoApproveSeconds: [0, 600], // 0 = off
  telegramLongRunMinutes: [1, 1440],
  loopMaxRounds: [0, 200], // 0 = no cap
  loopModelCalls: [5, 500],
  loopRepeatLimit: [2, 20],
  loopToolErrorLimit: [1, 10],
  loopDoomLimit: [1, 10],
  finalRoundSeconds: [10, 600],
  toolResultInlineChars: [2000, 60000],
  planLoopMaxSteps: [2, 20],
  planLoopVerifyPasses: [0, 3],
  parallelReads: [0, 8], // 0 = automatic
  llmRetries: [0, 10],
  llmIdleSeconds: [10, 3600],
  compactAt: [0.1, 0.95],
  compactAtTokens: [4000, 4000000],
  compactKeepRecent: [2, 200],
  compactKeepTokens: [1000, 1000000],
  microAt: [0.05, 0.95],
  microKeep: [0, 50],
  contextWindow: [0, 4000000], // 0 = automatic; the backend takes 1000 and up otherwise
  toolDeferAbove: [0, 200] // 0 = send every tool
} as const satisfies Record<string, readonly [number, number]>

/** A number input that clamps to its setting's range once, here: junk falls back to `fallback`, and so does 0 unless 0 is in the range. */
export default function NumberField({ name, value, fallback, step = 1, onChange, id, label }: {
  name: keyof typeof RANGES; value: number; fallback: number; step?: number; onChange: (n: number) => void
  id?: string; /** Accessible name when no wrapping label gives one. */ label?: string
}): JSX.Element {
  const range = RANGES[name]
  return (
    <input id={id} type="number" aria-label={label} min={range[0]} max={range[1]} step={step} value={value}
      onChange={(e) => onChange(clampNumber(e.target.value, range, step, fallback))} />
  )
}
