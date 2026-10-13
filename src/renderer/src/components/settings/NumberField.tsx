/** Accepted ranges of the numeric settings shown here; mirrors backend limits.RANGES (not served over the API). */
export const RANGES = {
  delegationAfterRounds: [1, 20],
  workerMaxConcurrent: [1, 16],
  codingSessionMaxConcurrent: [1, 20],
  autoArchiveDays: [0, 3650], // 0 = off; the backend takes 0 as "automatic" and 1 to 3650
  telegramLongRunMinutes: [1, 1440]
} as const satisfies Record<string, readonly [number, number]>

/** A number input that clamps to its setting's range once, here: junk and 0 fall back to `fallback`. */
export default function NumberField({ name, value, fallback, step = 1, onChange }: {
  name: keyof typeof RANGES; value: number; fallback: number; step?: number; onChange: (n: number) => void
}): JSX.Element {
  const [min, max] = RANGES[name]
  return (
    <input type="number" min={min} max={max} step={step} value={value}
      onChange={(e) => onChange(Math.min(max, Math.max(min, Math.round(Number(e.target.value) / step) * step || fallback)))} />
  )
}
