import { useEffect, useMemo, useState } from 'react'
import type { GoogleCalendar } from '@shared/types'
import { calendarShown, readVisibility, toggleCalendar, writeVisibility, type CalendarVisibility } from '../lib/calendarVisibility'
import { useStore } from '../store'
import { loadCalendarMeta } from './EventEditor'

/**
 * The calendar list plus which ones this window is showing.
 * `query` is what to pass as the calendars param: null while loading, '' when
 * everything is hidden, 'all' if the list itself failed to load.
 */
export function useVisibleCalendars(): {
  calendars: GoogleCalendar[]
  /** Ids currently drawn. Empty when every calendar is hidden. */
  visibleIds: string[]
  query: string | null
  ready: boolean
  shown: (c: GoogleCalendar) => boolean
  toggle: (c: GoogleCalendar) => void
} {
  const connected = useStore((s) => !!s.google?.connected)
  const [calendars, setCalendars] = useState<GoogleCalendar[]>([])
  const [pref, setPref] = useState<CalendarVisibility>(readVisibility)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    if (!connected) {
      setCalendars([])
      setReady(false)
      return
    }
    let alive = true
    void loadCalendarMeta().then((m) => {
      if (!alive) return
      setCalendars(m.calendars)
      setReady(true)
    }).catch(() => { if (alive) { setCalendars([]); setReady(true) } })
    return () => { alive = false }
  }, [connected])

  const visibleIds = useMemo(
    () => calendars.filter((c) => calendarShown(c, pref)).map((c) => c.id),
    [calendars, pref]
  )

  let query: string | null = null
  if (connected && ready) {
    if (calendars.length === 0) query = 'all'
    else if (visibleIds.length === 0) query = ''
    else query = visibleIds.join(',')
  }

  return {
    calendars,
    visibleIds,
    query,
    ready,
    shown: (c) => calendarShown(c, pref),
    toggle: (c) => setPref((prev) => writeVisibility(toggleCalendar(prev, c)))
  }
}
