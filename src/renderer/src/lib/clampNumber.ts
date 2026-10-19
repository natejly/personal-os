/** Clamp a typed number into [min, max] on a step grid. Junk and empty fall back; 0 falls back only when 0 is out of range. */
export function clampNumber(raw: string | number, [min, max]: readonly [number, number], step: number, fallback: number): number {
  const n = typeof raw === 'string' ? (raw.trim() === '' ? NaN : Number(raw)) : raw
  if (!Number.isFinite(n)) return fallback
  if (n === 0 && min > 0) return fallback
  const snapped = Math.round(n / step) * step
  const decimals = (String(step).split('.')[1] ?? '').length
  return Math.min(max, Math.max(min, Number(snapped.toFixed(decimals))))
}
