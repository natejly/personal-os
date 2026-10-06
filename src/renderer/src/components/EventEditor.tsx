import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, ExternalLink, Plus, Trash2, Video, X } from 'lucide-react'
import type { CalendarColors, CalendarEvent, EventPayload, GoogleCalendar } from '@shared/types'
import { api } from '../lib/api'
import { eventPrep } from '../lib/emailAsk'
import { readView, writeView } from '../lib/viewCache'
import { useStore } from '../store'

/** Seed for create mode: the slot the user dragged out or double-clicked, or an all-day cell.
 *
 * `start`/`end` are local wall-clock `YYYY-MM-DDTHH:MM` strings and win over `day`/`hour`, so a
 * dragged span keeps its exact minutes instead of being rounded back to the hour.
 */
export interface EventDraft { day?: string; hour?: number; title?: string; start?: string; end?: string; allDay?: boolean }

const pad = (n: number): string => String(n).padStart(2, '0')
const toDateInput = (d: Date): string => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const toDtInput = (iso: string): string => { const d = new Date(iso); return `${toDateInput(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}` }
const shiftDay = (day: string, n: number): string => { const d = new Date(`${day}T00:00:00`); d.setDate(d.getDate() + n); return toDateInput(d) }

const WEEKDAYS = ['SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA']
const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

/** Recurrence presets, derived from the start date like Google's own picker. */
const rrulePresets = (startDay: string): { key: string; label: string; rule: string }[] => {
  const d = new Date(`${startDay}T00:00:00`)
  return [
    { key: 'none', label: 'Does not repeat', rule: '' },
    { key: 'daily', label: 'Daily', rule: 'RRULE:FREQ=DAILY' },
    { key: 'weekly', label: `Weekly on ${DAY_NAMES[d.getDay()]}`, rule: `RRULE:FREQ=WEEKLY;BYDAY=${WEEKDAYS[d.getDay()]}` },
    { key: 'monthly', label: `Monthly on day ${d.getDate()}`, rule: 'RRULE:FREQ=MONTHLY' },
    { key: 'yearly', label: 'Annually', rule: 'RRULE:FREQ=YEARLY' },
    { key: 'weekdays', label: 'Every weekday (Mon–Fri)', rule: 'RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR' }
  ]
}

interface Reminder { method: 'popup' | 'email'; minutes: number }
const REMINDER_UNITS = [
  { label: 'minutes', mult: 1 },
  { label: 'hours', mult: 60 },
  { label: 'days', mult: 1440 },
  { label: 'weeks', mult: 10080 }
]
const splitMinutes = (m: number): { n: number; mult: number } => {
  for (const u of [...REMINDER_UNITS].reverse()) if (m > 0 && m % u.mult === 0) return { n: m / u.mult, mult: u.mult }
  return { n: m, mult: 1 }
}

// Calendars and the color palette change rarely; fetch once per app run and reuse.
let metaCache: { calendars: GoogleCalendar[]; colors: CalendarColors } | null = null
const loadMeta = async (): Promise<NonNullable<typeof metaCache>> => {
  if (!metaCache) metaCache = { calendars: await api.google.calendars(), colors: await api.google.calendarColors() }
  return metaCache
}
/** Resolve an event's display color: its own color, else its calendar's. */
export const eventColor = (e: CalendarEvent): string | null =>
  (e.color_id && metaCache?.colors.event[e.color_id]) || (e.calendar_id && metaCache?.calendars.find((c) => c.id === e.calendar_id)?.color) || null
/** Warm the calendar/color cache; resolves when eventColor() can answer. */
export const primeCalendarMeta = async (): Promise<void> => { await loadMeta().catch(() => undefined) }
/** The same fetch, for the calendar list. Rejects on failure so the caller can fall back. */
export const loadCalendarMeta = loadMeta

export interface EventEditorProps {
  /** Existing event to edit (list-grade is fine; the editor refetches full details). Null = create. */
  event: CalendarEvent | null
  draft?: EventDraft | null
  onClose: () => void
  /** Fired after any successful save or delete, so the caller can reload its event list. */
  onSaved: () => void
}

export default function EventEditor({ event, draft, onClose, onSaved }: EventEditorProps): JSX.Element {
  const toast = useStore((s) => s.toast)
  const isEdit = !!event
  const [meta, setMeta] = useState(metaCache)
  const [full, setFull] = useState<CalendarEvent | null>(null)
  const [loading, setLoading] = useState(isEdit)
  const [busy, setBusyState] = useState(false)
  // Recurring events: edit one occurrence, or the whole series (the master event).
  const [scope, setScope] = useState<'one' | 'all'>('one')

  const [title, setTitle] = useState(draft?.title ?? '')
  const [calendarId, setCalendarId] = useState('primary')
  const [origCalendarId, setOrigCalendarId] = useState('primary')
  const [allDay, setAllDay] = useState(false)
  const [startDt, setStartDt] = useState('')
  const [endDt, setEndDt] = useState('')
  const [startDay, setStartDay] = useState('')
  const [endDay, setEndDay] = useState('')
  const [rruleKey, setRruleKey] = useState('none')
  const [rruleCustom, setRruleCustom] = useState('')
  const [location, setLocation] = useState('')
  const [description, setDescription] = useState('')
  const [attendees, setAttendees] = useState<{ email: string; optional: boolean; response?: string | null; self?: boolean }[]>([])
  const [attendeeInput, setAttendeeInput] = useState('')
  const [meetLink, setMeetLink] = useState('')
  const [addMeet, setAddMeet] = useState(false)
  const [removeMeet, setRemoveMeet] = useState(false)
  const [remindDefault, setRemindDefault] = useState(true)
  const [reminders, setReminders] = useState<Reminder[]>([])
  const [colorId, setColorId] = useState('')
  const [showAsBusy, setShowAsBusy] = useState(true)
  const [visibility, setVisibility] = useState('default')
  const [gInvite, setGInvite] = useState(true)
  const [gModify, setGModify] = useState(false)
  const [gSee, setGSee] = useState(true)
  const [sendUpdates, setSendUpdates] = useState<'none' | 'all' | 'externalOnly'>('none')
  const [notifyTouched, setNotifyTouched] = useState(false)
  // A background refresh must not wipe a field the user has already changed.
  const dirty = useRef(false)

  const presets = useMemo(() => rrulePresets(allDay ? startDay || toDateInput(new Date()) : (startDt || new Date().toISOString()).slice(0, 10)), [allDay, startDay, startDt])

  useEffect(() => {
    void loadMeta().then((m) => {
      setMeta(m)
      // Create mode starts on the alias 'primary'; swap in the real calendar id once known.
      if (!isEdit) setCalendarId((cur) => (cur === 'primary' ? m.calendars.find((c) => c.primary)?.id ?? cur : cur))
    }).catch((e) => toast((e as Error).message, 'error'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toast])

  // Seed the form: from the draft slot (create) or the fully fetched event (edit).
  useEffect(() => {
    if (!isEdit) {
      const day = draft?.start?.slice(0, 10) ?? draft?.day ?? toDateInput(new Date())
      const hour = draft?.hour ?? new Date().getHours() + 1
      setStartDay(day); setEndDay(draft?.end?.slice(0, 10) ?? day)
      if (draft?.start) {
        setStartDt(draft.start.slice(0, 16))
        setEndDt((draft.end ?? draft.start).slice(0, 16))
      } else {
        setStartDt(`${day}T${pad(Math.min(hour, 23))}:00`)
        setEndDt(hour >= 23 ? `${day}T23:59` : `${day}T${pad(hour + 1)}:00`)
      }
      setAllDay(draft?.allDay ?? (!draft?.start && draft?.hour === undefined && !!draft?.day))
      const primary = metaCache?.calendars.find((c) => c.primary)
      if (primary) setCalendarId(primary.id)
      setLoading(false)
      return
    }
    let alive = true
    dirty.current = false
    const cid = event.calendar_id || 'primary'
    const targetId = scope === 'all' && event.recurring_event_id ? event.recurring_event_id : event.id
    const apply = (f: CalendarEvent): void => {
      setFull(f)
      setTitle(f.summary === '(no title)' ? '' : f.summary)
      setCalendarId(cid); setOrigCalendarId(cid)
      setAllDay(f.all_day)
      if (f.all_day) {
        setStartDay(f.start)
        setEndDay(shiftDay(f.end, -1)) // exclusive end -> last day shown inclusive, like Google
      } else {
        setStartDt(toDtInput(f.start)); setEndDt(toDtInput(f.end))
        setStartDay(toDtInput(f.start).slice(0, 10)); setEndDay(toDtInput(f.end).slice(0, 10))
      }
      const rules = f.recurrence ?? []
      const preset = rrulePresets(f.all_day ? f.start : toDtInput(f.start).slice(0, 10)).find((p) => rules.length === 1 && rules[0] === p.rule)
      setRruleKey(rules.length === 0 ? 'none' : preset ? preset.key : 'custom')
      setRruleCustom(rules.join('\n'))
      setLocation(f.location ?? '')
      setDescription(f.description ?? '')
      // Keep everyone, self included, and carry their RSVP through: a patched attendee
      // list replaces the old one, and a guest sent without responseStatus loses theirs.
      setAttendees((f.attendee_details ?? []).map((a) => ({ email: a.email, optional: a.optional, response: a.response, self: a.self })))
      setMeetLink(f.meet); setAddMeet(false); setRemoveMeet(false)
      setRemindDefault(f.reminders?.useDefault ?? true)
      setReminders((f.reminders?.overrides ?? []).map((o) => ({ method: o.method === 'email' ? 'email' : 'popup', minutes: o.minutes })))
      setColorId(f.color_id ?? '')
      setShowAsBusy((f.transparency ?? 'opaque') !== 'transparent')
      setVisibility(f.visibility ?? 'default')
      setGInvite(f.guests_can_invite_others ?? true)
      setGModify(f.guests_can_modify ?? false)
      setGSee(f.guests_can_see_other_guests ?? true)
      setLoading(false)
    }
    const key = `event:${cid}:${targetId}`
    const cached = readView<CalendarEvent>(key)
    if (cached) apply(cached)
    else setLoading(true)
    api.google.getEvent(targetId, cid).then((f) => {
      if (!alive) return
      writeView(key, f)
      if (!dirty.current) apply(f)
      else setLoading(false)
    }).catch((e) => { if (alive && !cached) { toast((e as Error).message, 'error'); onClose() } })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isEdit, event?.id, scope])

  // Default to emailing guests once there are any, until the user picks otherwise.
  useEffect(() => {
    if (!notifyTouched) setSendUpdates(attendees.length > 0 ? 'all' : 'none')
  }, [attendees.length, notifyTouched])

  // Escape and a stray backdrop click are the accidental ways out, so they ask before throwing away
  // edits; the Cancel and X buttons are deliberate and close straight away.
  // `dirty` also flips on a plain click inside the form, so it cannot be what asks: only a field that
  // actually changed does.
  const edited = useRef(false)
  const requestClose = useCallback((): void => {
    if (edited.current && !window.confirm('Discard your changes to this event?')) return
    onClose()
  }, [onClose])
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => { if (e.key === 'Escape') requestClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [requestClose])

  const writableCalendars = (meta?.calendars ?? []).filter((c) => c.primary || c.access_role === 'owner' || c.access_role === 'writer')
  const isRecurring = !!(event?.recurring_event_id || (full?.recurrence?.length ?? 0) > 0)
  // Recurrence itself only lives on the series master; a single occurrence cannot repeat.
  const canEditRecurrence = !isEdit || scope === 'all' || !event?.recurring_event_id

  const addAttendee = (): void => {
    const email = attendeeInput.trim().replace(/,$/, '')
    if (!email) return
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { toast(`"${email}" is not an email address`, 'error'); return }
    if (!attendees.some((a) => a.email === email)) setAttendees([...attendees, { email, optional: false }])
    setAttendeeInput('')
  }

  const rruleLines = (): string[] => {
    if (rruleKey === 'none') return []
    if (rruleKey === 'custom') return rruleCustom.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => (l.toUpperCase().startsWith('RRULE') || l.toUpperCase().startsWith('EXDATE') || l.toUpperCase().startsWith('RDATE') ? l : `RRULE:${l}`))
    return [presets.find((p) => p.key === rruleKey)?.rule ?? ''].filter(Boolean)
  }

  const save = async (): Promise<void> => {
    if (!title.trim()) { toast('The event needs a title', 'error'); return }
    if (allDay ? !startDay : !startDt) { toast('The event needs a start', 'error'); return }
    if (!allDay && (Number.isNaN(Date.parse(startDt)) || (endDt && Number.isNaN(Date.parse(endDt))))) { toast('The start or end is not a valid date and time', 'error'); return }
    const payload: EventPayload = {
      summary: title.trim(),
      start: allDay ? startDay : `${startDt}:00`,
      // All-day ends are exclusive in the API; the form shows the inclusive last day.
      end: allDay ? shiftDay(endDay && endDay >= startDay ? endDay : startDay, 1) : endDt ? `${endDt}:00` : undefined,
      description,
      location,
      attendees: attendees.map((a) => ({ email: a.email, optional: a.optional, response: a.response ?? undefined })),
      reminders: remindDefault ? { use_default: true } : { use_default: false, overrides: reminders },
      color_id: colorId,
      visibility,
      transparency: showAsBusy ? 'opaque' : 'transparent',
      guests_can_invite_others: gInvite,
      guests_can_modify: gModify,
      guests_can_see_other_guests: gSee,
      send_updates: sendUpdates
    }
    if (canEditRecurrence) payload.recurrence = rruleLines()
    if (!allDay && endDt && startDt && endDt <= startDt) { toast('The end must be after the start', 'error'); return }
    setBusyState(true)
    try {
      if (isEdit && event) {
        const targetId = scope === 'all' && event.recurring_event_id ? event.recurring_event_id : event.id
        payload.calendar_id = origCalendarId
        if (calendarId !== origCalendarId) payload.move_to_calendar_id = calendarId
        if (addMeet && !meetLink) payload.create_meet = true
        if (removeMeet && meetLink) payload.clear_meet = true
        await api.google.updateEvent(targetId, payload)
        toast('Event updated')
      } else {
        payload.calendar_id = calendarId
        if (addMeet) payload.create_meet = true
        await api.google.createEvent(payload as EventPayload & { summary: string; start: string })
        toast(`Added "${title.trim()}"`)
      }
      onSaved(); onClose()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusyState(false)
    }
  }

  const remove = async (): Promise<void> => {
    if (!event) return
    const series = scope === 'all' && event.recurring_event_id
    if (!window.confirm(series ? 'Delete the whole series?' : 'Delete this event?')) return
    setBusyState(true)
    try {
      await api.google.deleteEvent(series ? event.recurring_event_id! : event.id, origCalendarId, sendUpdates)
      toast('Event deleted')
      onSaved(); onClose()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusyState(false)
    }
  }

  const respond = async (r: 'accepted' | 'declined' | 'tentative'): Promise<void> => {
    if (!event) return
    setBusyState(true)
    try {
      await api.google.respondEvent(event.id, r, origCalendarId)
      toast(r === 'accepted' ? 'Going' : r === 'declined' ? 'Declined' : 'Maybe')
      onSaved(); onClose()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusyState(false)
    }
  }

  const me = full?.attendee_details?.find((a) => a.self)
  const palette = meta?.colors.event ?? {}
  const calColor = meta?.calendars.find((c) => c.id === calendarId)?.color ?? null
  const showLink = full?.link || event?.link || ''

  const prep = (): void => {
    const name = title || event?.summary || 'this event'
    const when = allDay ? (startDay || event?.start || '') : startDt ? new Date(startDt).toLocaleString() : event ? new Date(event.start).toLocaleString() : ''
    const who = attendees.filter((a) => !a.self).map((a) => a.email)
    const { newChat, noteUntrustedUpload, send } = useStore.getState()
    onClose()
    newChat(null)
    void (async () => {
      await noteUntrustedUpload(undefined, 'draft', 'calendar')
      await send(eventPrep(name, when, who))
    })()
  }

  return (
    <div className="modal-backdrop" onMouseDown={requestClose}>
      <div className="modal event-editor" onMouseDown={(e) => e.stopPropagation()}
        onChange={() => { dirty.current = true; edited.current = true }} onClickCapture={() => { if (!loading) dirty.current = true }}>
        <header>
          <h2>{isEdit ? 'Edit event' : 'New event'}</h2>
          <button className="icon-btn" aria-label="Close" onClick={onClose}><X size={16} /></button>
        </header>

        {loading ? (
          <>
            <section><p className="muted">Loading…</p></section>
            {isEdit && (
              <footer>
                {showLink && <a className="ghost-btn" href={showLink} target="_blank" rel="noreferrer"><ExternalLink size={13} /> Show in calendar</a>}
                <button className="ghost-btn" onClick={prep}>Prep me</button>
                <span style={{ flex: 1 }} />
                <button className="ghost-btn" onClick={onClose}>Cancel</button>
              </footer>
            )}
          </>
        ) : (
          <>
            {isEdit && isRecurring && event?.recurring_event_id && (
              <section className="ev-scope">
                <span className="muted small">Repeating event — change:</span>
                <div className="seg">
                  <button className={scope === 'one' ? 'on' : ''} onClick={() => setScope('one')}>This event</button>
                  <button className={scope === 'all' ? 'on' : ''} onClick={() => setScope('all')}>All events</button>
                </div>
              </section>
            )}

            <section>
              <label>
                <input className="ev-title" autoFocus placeholder="Add title" value={title} onChange={(e) => setTitle(e.target.value)} />
              </label>

              <div className="ev-row">
                <label className="grow">
                  <span>Calendar</span>
                  <div className="input-row">
                    {calColor && <span className="ev-dot" style={{ background: calColor }} />}
                    <select value={calendarId} onChange={(e) => setCalendarId(e.target.value)}>
                      {writableCalendars.map((c) => <option key={c.id} value={c.id}>{c.summary}</option>)}
                      {!writableCalendars.some((c) => c.id === calendarId) && <option value={calendarId}>{calendarId}</option>}
                    </select>
                  </div>
                </label>
                <label className="ev-check">
                  <span>&nbsp;</span>
                  <span className="input-row"><input type="checkbox" checked={allDay} onChange={(e) => setAllDay(e.target.checked)} /> All day</span>
                </label>
              </div>

              <div className="ev-row">
                {allDay ? (
                  <>
                    <label className="grow"><span>Start</span><input type="date" value={startDay} onChange={(e) => { setStartDay(e.target.value); if (endDay < e.target.value) setEndDay(e.target.value) }} /></label>
                    <label className="grow"><span>End</span><input type="date" min={startDay} value={endDay || startDay} onChange={(e) => setEndDay(e.target.value)} /></label>
                  </>
                ) : (
                  <>
                    <label className="grow"><span>Start</span><input type="datetime-local" value={startDt} onChange={(e) => {
                      const v = e.target.value
                      // A half-cleared field reads as '': keep the last good start rather than derive an end from NaN.
                      if (!v || Number.isNaN(Date.parse(v))) return
                      // Keep the duration when the start moves, like Google does.
                      if (startDt && endDt) {
                        const dur = new Date(endDt).getTime() - new Date(startDt).getTime()
                        if (dur > 0) setEndDt(toDtInput(new Date(new Date(v).getTime() + dur).toISOString()))
                      }
                      setStartDt(v)
                    }} /></label>
                    <label className="grow"><span>End</span><input type="datetime-local" min={startDt} value={endDt} onChange={(e) => setEndDt(e.target.value)} /></label>
                  </>
                )}
              </div>

              {canEditRecurrence ? (
                <label>
                  <span>Repeats</span>
                  <select value={rruleKey} onChange={(e) => setRruleKey(e.target.value)}>
                    {presets.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
                    <option value="custom">Custom (RRULE)…</option>
                  </select>
                </label>
              ) : (
                isRecurring && <p className="muted small">Repeats — switch to “All events” to change the recurrence.</p>
              )}
              {canEditRecurrence && rruleKey === 'custom' && (
                <label>
                  <span>RRULE lines (e.g. RRULE:FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261231)</span>
                  <textarea rows={2} value={rruleCustom} onChange={(e) => setRruleCustom(e.target.value)} spellCheck={false} />
                </label>
              )}
            </section>

            <section>
              <label>
                <span>Guests</span>
                <input placeholder="Add guest email, press Enter" value={attendeeInput}
                  onChange={(e) => setAttendeeInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addAttendee() } }}
                  onBlur={addAttendee} />
              </label>
              {attendees.length > 0 && (
                <div className="ev-chips">
                  {attendees.map((a) => (
                    <span key={a.email} className={`ev-chip ${a.optional ? 'optional' : ''}`}>
                      {a.email}{a.self ? ' (you)' : ''}
                      <button title={a.optional ? 'Mark required' : 'Mark optional'}
                        onClick={() => setAttendees(attendees.map((x) => (x.email === a.email ? { ...x, optional: !x.optional } : x)))}>{a.optional ? 'optional' : 'req'}</button>
                      <button title="Remove" aria-label={`Remove ${a.email}`} onClick={() => setAttendees(attendees.filter((x) => x.email !== a.email))}><X size={11} /></button>
                    </span>
                  ))}
                </div>
              )}
              {attendees.length > 0 && (
                <div className="ev-perms">
                  <label className="inline"><input type="checkbox" checked={gInvite} onChange={(e) => setGInvite(e.target.checked)} /> Guests can invite others</label>
                  <label className="inline"><input type="checkbox" checked={gModify} onChange={(e) => setGModify(e.target.checked)} /> Guests can modify event</label>
                  <label className="inline"><input type="checkbox" checked={gSee} onChange={(e) => setGSee(e.target.checked)} /> Guests can see guest list</label>
                </div>
              )}

              {meetLink && !removeMeet ? (
                <div className="ev-meet">
                  <Video size={14} />
                  <a href={meetLink} target="_blank" rel="noreferrer">{meetLink.replace('https://', '')}</a>
                  <button className="ghost-btn" onClick={() => setRemoveMeet(true)}>Remove</button>
                </div>
              ) : (
                <label className="inline">
                  <input type="checkbox" checked={addMeet} onChange={(e) => { setAddMeet(e.target.checked); if (e.target.checked) setRemoveMeet(false) }} />
                  <Video size={14} /> {meetLink ? 'Keep a Google Meet link' : 'Add Google Meet video conferencing'}
                </label>
              )}
              {meetLink && removeMeet && <p className="muted small">Meet link will be removed on save. <button className="link" onClick={() => setRemoveMeet(false)}>Undo</button></p>}

              <label><span>Location</span><input placeholder="Add location" value={location} onChange={(e) => setLocation(e.target.value)} /></label>
              <label><span>Description</span><textarea rows={3} placeholder="Add description" value={description} onChange={(e) => setDescription(e.target.value)} /></label>
            </section>

            <section>
              <label className="inline"><input type="checkbox" checked={remindDefault} onChange={(e) => setRemindDefault(e.target.checked)} /> Use the calendar's default notifications</label>
              {!remindDefault && (
                <div className="ev-reminders">
                  {reminders.map((r, i) => {
                    const { n, mult } = splitMinutes(r.minutes)
                    return (
                      <div key={i} className="ev-row">
                        <select value={r.method} onChange={(e) => setReminders(reminders.map((x, j) => (j === i ? { ...x, method: e.target.value as Reminder['method'] } : x)))}>
                          <option value="popup">Notification</option>
                          <option value="email">Email</option>
                        </select>
                        <input type="number" min={0} value={n} onChange={(e) => setReminders(reminders.map((x, j) => (j === i ? { ...x, minutes: Math.max(0, Number(e.target.value)) * mult } : x)))} />
                        <select value={mult} onChange={(e) => setReminders(reminders.map((x, j) => (j === i ? { ...x, minutes: n * Number(e.target.value) } : x)))}>
                          {REMINDER_UNITS.map((u) => <option key={u.mult} value={u.mult}>{u.label} before</option>)}
                        </select>
                        <button className="icon-btn" title="Remove" onClick={() => setReminders(reminders.filter((_, j) => j !== i))}><X size={13} /></button>
                      </div>
                    )
                  })}
                  {reminders.length < 5 && (
                    <button className="ghost-btn" onClick={() => setReminders([...reminders, { method: 'popup', minutes: 10 }])}><Plus size={13} /> Add notification</button>
                  )}
                </div>
              )}

              <div className="ev-row">
                <label className="grow">
                  <span>Color</span>
                  <div className="ev-colors">
                    <button className={`ev-swatch ${colorId === '' ? 'on' : ''}`} title="Calendar color" aria-label="Calendar color" aria-pressed={colorId === ''} style={{ background: calColor ?? 'var(--border-strong)' }} onClick={() => setColorId('')}>{colorId === '' && <Check size={11} />}</button>
                    {Object.entries(palette).map(([id, hex]) => (
                      <button key={id} className={`ev-swatch ${colorId === id ? 'on' : ''}`} aria-label={`Color ${id}`} aria-pressed={colorId === id} style={{ background: hex }} onClick={() => setColorId(id)}>{colorId === id && <Check size={11} />}</button>
                    ))}
                  </div>
                </label>
              </div>

              <div className="ev-row">
                <label className="grow">
                  <span>Show as</span>
                  <select value={showAsBusy ? 'busy' : 'free'} onChange={(e) => setShowAsBusy(e.target.value === 'busy')}>
                    <option value="busy">Busy</option>
                    <option value="free">Free</option>
                  </select>
                </label>
                <label className="grow">
                  <span>Visibility</span>
                  <select value={visibility} onChange={(e) => setVisibility(e.target.value)}>
                    <option value="default">Default visibility</option>
                    <option value="public">Public</option>
                    <option value="private">Private</option>
                  </select>
                </label>
              </div>
            </section>

            {isEdit && me && (
              <section className="ev-rsvp">
                <span className="muted small">Going?</span>
                <div className="seg">
                  <button className={me.response === 'accepted' ? 'on' : ''} disabled={busy} onClick={() => void respond('accepted')}>Yes</button>
                  <button className={me.response === 'declined' ? 'on' : ''} disabled={busy} onClick={() => void respond('declined')}>No</button>
                  <button className={me.response === 'tentative' ? 'on' : ''} disabled={busy} onClick={() => void respond('tentative')}>Maybe</button>
                </div>
                {full?.organizer && <span className="muted small">Organizer: {full.organizer}</span>}
              </section>
            )}

            <footer>
              {isEdit && <button className="ghost-btn danger" disabled={busy} onClick={() => void remove()}><Trash2 size={13} /> Delete</button>}
              {isEdit && showLink && <a className="ghost-btn" href={showLink} target="_blank" rel="noreferrer"><ExternalLink size={13} /> Show in calendar</a>}
              {isEdit && <button className="ghost-btn" onClick={prep}>Prep me</button>}
              <span style={{ flex: 1 }} />
              {attendees.length > 0 && (
                <select className="ev-notify" title="Email the guests about this change" value={sendUpdates}
                  onChange={(e) => { setNotifyTouched(true); setSendUpdates(e.target.value as typeof sendUpdates) }}>
                  <option value="all">Email guests</option>
                  <option value="externalOnly">Email non-Google guests</option>
                  <option value="none">Don't email guests</option>
                </select>
              )}
              <button className="ghost-btn" onClick={onClose}>Cancel</button>
              <button className="primary-btn" disabled={busy} onClick={() => void save()}>{busy ? 'Saving…' : 'Save'}</button>
            </footer>
          </>
        )}
      </div>
    </div>
  )
}
