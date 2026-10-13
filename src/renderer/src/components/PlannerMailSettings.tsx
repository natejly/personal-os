import { useEffect, useState } from 'react'
import { api, type MailWatchConfig, type PlannerConfig } from '../lib/api'
import type { GoogleCalendar } from '@shared/types'
import { useStore } from '../store'
import Switch from './settings/Switch'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

/** Day plan work hours and reply tracker thresholds. Each change is saved through its module's config route
 *  right away (the backend validates it), like the Google credentials above it. */
export default function PlannerMailSettings(): JSX.Element | null {
  const toast = useStore((s) => s.toast)
  const [plan, setPlan] = useState<PlannerConfig | null>(null)
  const [mail, setMail] = useState<MailWatchConfig | null>(null)
  const [cals, setCals] = useState<GoogleCalendar[] | null | 'failed'>(null)
  const connected = useStore((s) => s.google?.connected)
  // Not signed in to Google: the calendar list is a 409, so only ask once there is an account.
  useEffect(() => {
    if (connected) api.google.calendars().then(setCals).catch(() => setCals('failed'))
    else if (connected === false) setCals('failed')
  }, [connected])
  useEffect(() => {
    api.planner.config().then(setPlan).catch(() => undefined)
    api.mailWatch.config().then(setMail).catch(() => undefined)
  }, [])
  const savePlan = (p: Partial<PlannerConfig>): void => {
    api.planner.setConfig(p).then(setPlan).catch((e: Error) => toast(e.message, 'error'))
  }
  const saveMail = (p: Partial<MailWatchConfig>): void => {
    api.mailWatch.setConfig(p).then(setMail).catch((e: Error) => toast(e.message, 'error'))
  }
  if (!plan && !mail) return null
  return (
    <>
      {plan && <>
        <h4>Day plan</h4>
        <label className="inline"><span>Work hours</span>
          <input type="time" defaultValue={plan.workStart} key={`s${plan.workStart}`} onBlur={(e) => e.target.value !== plan.workStart && savePlan({ workStart: e.target.value })} />
          <span>to</span>
          <input type="time" defaultValue={plan.workEnd} key={`e${plan.workEnd}`} onBlur={(e) => e.target.value !== plan.workEnd && savePlan({ workEnd: e.target.value })} />
        </label>
        <div className="input-row" role="group" aria-label="Work days">
          {DAYS.map((d, i) => {
            const n = i + 1
            const on = plan.workDays.includes(n)
            return (
              <label key={d} className="check">
                <input type="checkbox" checked={on} onChange={() => savePlan({ workDays: on ? plan.workDays.filter((x) => x !== n) : [...plan.workDays, n].sort() })} />
                {d}
              </label>
            )
          })}
        </div>
        <p className="muted small">Calendars that count as busy</p>
        {Array.isArray(cals) && cals.length > 0 ? <>
          <p className="muted small">{plan.busyCalendarIds.length ? 'Only the calendars ticked below block time.' : 'Every calendar you have checked on in Google blocks time. Tick one to choose exactly which.'}</p>
          <div className="input-row" role="group" aria-label="Calendars that count as busy">
            {cals.map((c) => {
              const on = plan.busyCalendarIds.includes(c.id)
              return (
                <label key={c.id} className="check">
                  <input type="checkbox" checked={on} onChange={() => savePlan({ busyCalendarIds: on ? plan.busyCalendarIds.filter((x) => x !== c.id) : [...plan.busyCalendarIds, c.id] })} />
                  {c.summary}{c.primary ? ' (primary)' : ''}
                </label>
              )
            })}
          </div>
        </> : <p className="muted small">{cals === null ? 'Loading calendars...' : 'Calendars could not be loaded. Every calendar you have checked on in Google blocks time.'}</p>}
      </>}
      {mail && <>
        <h4>Reply tracker</h4>
        <Switch title="Track replies" help="Read recent Gmail threads to find mail you owe a reply and mail waiting on someone else." checked={mail.enabled} onChange={(v) => saveMail({ enabled: v })} />
        <label className="inline"><span>Needs a reply after</span>
          <input type="number" min={1} max={720} defaultValue={mail.needsReplyAfterHours} key={`h${mail.needsReplyAfterHours}`} onBlur={(e) => Number(e.target.value) !== mail.needsReplyAfterHours && saveMail({ needsReplyAfterHours: Number(e.target.value) })} />
          <span>hours</span>
        </label>
        <label className="inline"><span>Awaiting a reply after</span>
          <input type="number" min={1} max={60} defaultValue={mail.awaitingAfterDays} key={`d${mail.awaitingAfterDays}`} onBlur={(e) => Number(e.target.value) !== mail.awaitingAfterDays && saveMail({ awaitingAfterDays: Number(e.target.value) })} />
          <span>days</span>
        </label>
      </>}
    </>
  )
}
