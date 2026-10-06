/** Pure helpers for the scheduled-task form: schedule presets that compile to a 5-field cron, and the patch an edit sends. */

export type Preset = 'hourly' | 'daily' | 'weekdays' | 'weekly' | 'custom'
export interface Schedule {
  preset: Preset
  /** HH:MM, local to the job's timezone. Unused by 'hourly' and 'custom'. */
  time: string
  /** 0 = Sunday … 6 = Saturday, as cron counts. Only 'weekly' reads it. */
  day: number
  /** The raw expression; only 'custom' reads it. */
  cron: string
}

export const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
export const DEFAULT_SCHEDULE: Schedule = { preset: 'daily', time: '09:00', day: 1, cron: '' }

/** The cron expression a schedule stands for. Empty when the time is unreadable, so the form stays unready. */
export function presetCron(s: Schedule): string {
  if (s.preset === 'custom') return s.cron.trim()
  if (s.preset === 'hourly') return '0 * * * *'
  const m = /^(\d{1,2}):(\d{2})$/.exec(s.time)
  if (!m) return ''
  const at = `${Number(m[2])} ${Number(m[1])}`
  return s.preset === 'daily' ? `${at} * * *` : s.preset === 'weekdays' ? `${at} * * 1-5` : `${at} * * ${s.day}`
}

/** Read an existing expression back into a preset, so editing a job shows the choice it was made with. */
export function cronPreset(cron: string): Schedule {
  const expr = cron.trim().split(/\s+/).join(' ')
  if (expr === '0 * * * *') return { ...DEFAULT_SCHEDULE, preset: 'hourly' }
  const m = /^(\d{1,2}) (\d{1,2}) \* \* (\*|1-5|[0-6])$/.exec(expr)
  if (!m || Number(m[1]) > 59 || Number(m[2]) > 23) return { ...DEFAULT_SCHEDULE, preset: 'custom', cron: expr }
  const time = `${m[2].padStart(2, '0')}:${m[1].padStart(2, '0')}`
  if (m[3] === '*') return { ...DEFAULT_SCHEDULE, preset: 'daily', time }
  if (m[3] === '1-5') return { ...DEFAULT_SCHEDULE, preset: 'weekdays', time }
  return { ...DEFAULT_SCHEDULE, preset: 'weekly', time, day: Number(m[3]) }
}

/** A unix timestamp as a datetime-local value (local wall time, minute precision). */
export function toLocalInput(ts: number): string {
  const d = new Date(ts * 1000)
  const p = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

/** Only the keys of `after` whose value differs from `before`: an edit sends what changed and nothing else. */
export function diffJob<T extends object>(before: Partial<T>, after: Partial<T>): Partial<T> {
  const out: Partial<T> = {}
  for (const k of Object.keys(after) as (keyof T)[]) {
    if (JSON.stringify(after[k]) !== JSON.stringify(before[k])) out[k] = after[k]
  }
  return out
}
