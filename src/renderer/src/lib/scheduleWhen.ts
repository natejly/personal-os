/**
 * The composer's schedule form (`/schedule`, `/loop`): a preset or a custom value, turned into the timing
 * POST /jobs takes. Pure; `now` is passed in.
 */
export interface WhenPreset { id: string; label: string }

export const ONCE_PRESETS: WhenPreset[] = [
  { id: 'in1h', label: 'In 1 hour' },
  { id: 'tonight', label: 'Tonight 6pm' },
  { id: 'tomorrow9', label: 'Tomorrow 9am' },
  { id: 'monday9', label: 'Monday 9am' },
  { id: 'custom', label: 'Custom…' }
]

export const LOOP_PRESETS: WhenPreset[] = [
  { id: 'daily9', label: 'Every day at 9' },
  { id: 'weekday9', label: 'Every weekday at 9' },
  { id: 'hours', label: 'Every N hours' },
  { id: 'weekly', label: 'Every Monday at 9' },
  { id: 'custom', label: 'Custom (cron)…' }
]

export type JobTiming = { kind: 'once'; run_at: number } | { kind: 'cron'; cron: string }

const at = (d: Date, days: number, hour: number): Date => {
  const x = new Date(d)
  x.setDate(x.getDate() + days)
  x.setHours(hour, 0, 0, 0)
  return x
}
const secs = (d: Date): number => Math.floor(d.getTime() / 1000)

/**
 * The timing for a preset, or an error line. `custom` is a `datetime-local` value for a one-off, a five-field
 * cron for a loop (the backend checks it again); `hours` is the N of "every N hours".
 */
export function jobTiming(preset: string, now: Date, opts: { custom?: string; hours?: number } = {}): JobTiming | { error: string } {
  switch (preset) {
    case 'in1h': return { kind: 'once', run_at: secs(now) + 3600 }
    case 'tonight': {
      const t = at(now, 0, 18)
      return { kind: 'once', run_at: secs(t > now ? t : at(now, 1, 18)) }
    }
    case 'tomorrow9': return { kind: 'once', run_at: secs(at(now, 1, 9)) }
    case 'monday9': return { kind: 'once', run_at: secs(at(now, ((8 - now.getDay()) % 7) || 7, 9)) }
    case 'daily9': return { kind: 'cron', cron: '0 9 * * *' }
    case 'weekday9': return { kind: 'cron', cron: '0 9 * * 1-5' }
    case 'weekly': return { kind: 'cron', cron: '0 9 * * 1' }
    case 'hours': {
      const n = Math.round(opts.hours ?? 0)
      if (!(n >= 1 && n <= 23)) return { error: 'Pick between 1 and 23 hours.' }
      return { kind: 'cron', cron: n === 1 ? '0 * * * *' : `0 */${n} * * *` }
    }
  }
  return { error: 'Pick when it should run.' }
}

/** A custom value: `datetime-local` text for a one-off (must be in the future), cron text for a loop. */
export function customTiming(loop: boolean, value: string, now: Date): JobTiming | { error: string } {
  const v = value.trim()
  if (loop) return v.split(/\s+/).length === 5 ? { kind: 'cron', cron: v } : { error: 'A cron has five fields, e.g. 0 9 * * 1-5.' }
  const t = new Date(v)
  if (!v || Number.isNaN(t.getTime())) return { error: 'Pick a date and time.' }
  return t > now ? { kind: 'once', run_at: secs(t) } : { error: 'That time has already passed.' }
}
