import type { GoogleCalendar } from '@shared/types'

/** Which calendars this window has overridden. Google's own checkbox is the default. */
export interface CalendarVisibility {
  /** Turned on here, even though Google has them unchecked. */
  show: string[]
  /** Turned off here. */
  hide: string[]
}

const KEY = 'grain.calendar.visibility'

const ids = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])

export function readVisibility(): CalendarVisibility {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? '') as { show?: unknown; hide?: unknown }
    return { show: ids(raw?.show), hide: ids(raw?.hide) }
  } catch {
    return { show: [], hide: [] }
  }
}

export function writeVisibility(v: CalendarVisibility): CalendarVisibility {
  try {
    localStorage.setItem(KEY, JSON.stringify({ show: v.show, hide: v.hide }))
  } catch { /* a private window still toggles; it just forgets on reload */ }
  return v
}

type CalFlag = Pick<GoogleCalendar, 'id' | 'hidden' | 'selected' | 'primary'>

/** Google's checkbox, unless this window has an override. Primary stays on unless hidden here. */
export function calendarShown(c: CalFlag, pref: CalendarVisibility): boolean {
  if (pref.hide.includes(c.id)) return false
  if (pref.show.includes(c.id)) return true
  return !c.hidden && (c.selected || c.primary)
}

/** Flip one calendar. The stored lists only record a difference from Google's own checkbox. */
export function toggleCalendar(pref: CalendarVisibility, c: CalFlag): CalendarVisibility {
  const show = pref.show.filter((id) => id !== c.id)
  const hide = pref.hide.filter((id) => id !== c.id)
  const googleOn = !c.hidden && (c.selected || c.primary)
  if (calendarShown(c, pref)) return googleOn ? { show, hide: [...hide, c.id] } : { show, hide }
  return googleOn ? { show, hide } : { show: [...show, c.id], hide }
}
