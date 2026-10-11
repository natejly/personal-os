import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { CodingSession, CodingSessionEvent } from '@shared/types'
import { api } from '../lib/api'
import { eventSummary, isStepEvent, latestActivity } from '../lib/codingSessions'
import { nextStick } from '../lib/stickToBottom'
import { useStore } from '../store'

const NONE: CodingSessionEvent[] = []

/** A session's timeline from the store; loads the backlog once when `load` is set, the event stream keeps it current. */
export function useCodingEvents(id: string, load = true): CodingSessionEvent[] {
  const events = useStore((s) => s.codingEvents[id]) ?? NONE
  const loadEvents = useStore((s) => s.loadCodingEvents)
  useEffect(() => { if (load) void loadEvents(id) }, [id, load, loadEvents])
  return events
}

/** One timeline entry. Tool calls are a compact "name  summary" line; errors are red; a status change is a divider. */
export function EventRow({ e }: { e: CodingSessionEvent }): JSX.Element | null {
  if (e.kind === 'status') return <div className="cs-ev-status"><span>{e.text || 'status changed'}</span></div>
  if (isStepEvent(e)) return <div className="cs-ev cs-ev-result" title={e.text}>{eventSummary(e)}</div>
  if (e.kind === 'tool') {
    return (
      <div className="cs-ev cs-ev-tool" title={e.text}>
        <span className="cs-ev-name">{e.tool ?? 'tool'}</span>
        <span className="cs-ev-sum">{e.text.replace(/\s+/g, ' ').trim()}</span>
      </div>
    )
  }
  if (e.kind === 'tool_result') return e.text.trim() ? <div className="cs-ev cs-ev-result" title={e.text}>{e.text.replace(/\s+/g, ' ').trim()}</div> : null
  if (e.kind === 'error') return <div className="cs-ev cs-ev-error">{e.text}</div>
  if (e.kind === 'user') return <div className="cs-ev cs-ev-user"><span className="cs-ev-who">You</span>{e.text}</div>
  return <div className="cs-ev cs-ev-text">{e.text}</div>
}

/** The latest activity line plus the last few events: the inline feedback while a session works. */
export function CodingActivityPreview({ id, count = 3, load = true }: { id: string; count?: number; load?: boolean }): JSX.Element | null {
  const events = useCodingEvents(id, load)
  const last = latestActivity(events)
  const shown = events.filter((e) => e.kind !== 'tool_result').slice(-count)
  if (!last && !shown.length) return null
  return (
    <div className="cs-preview">
      {last && <div className="cs-latest" title={last.text}>{eventSummary(last)}</div>}
      <div className="cs-timeline compact">{shown.map((e) => <EventRow key={e.seq} e={e} />)}</div>
    </div>
  )
}

/** The whole timeline, following the bottom unless the user has scrolled up. */
export function CodingTimeline({ id }: { id: string }): JSX.Element {
  const events = useCodingEvents(id)
  const ref = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  const prevTop = useRef(0)
  const onScroll = (): void => {
    const el = ref.current
    if (!el) return
    stick.current = nextStick(stick.current, { top: el.scrollTop, prevTop: prevTop.current, distance: el.scrollHeight - el.scrollTop - el.clientHeight })
    prevTop.current = el.scrollTop
  }
  useLayoutEffect(() => {
    const el = ref.current
    if (el && stick.current) { el.scrollTop = el.scrollHeight; prevTop.current = el.scrollTop }
  }, [events])
  return (
    <div className="cs-timeline" ref={ref} onScroll={onScroll} role="log" aria-label="Coding session activity">
      {events.length ? events.map((e) => <EventRow key={e.seq} e={e} />) : <div className="cs-ev cs-ev-result">No activity yet.</div>}
    </div>
  )
}

/** Send a follow-up to a finished or stopped session; the session row comes back from the route. */
export function CodingFollowUp({ c }: { c: CodingSession }): JSX.Element | null {
  const upsert = useStore((s) => s.upsertCodingSession)
  const toast = useStore((s) => s.toast)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  if (c.status !== 'done' && c.status !== 'stopped') return null
  const send = async (): Promise<void> => {
    const message = text.trim()
    if (!message || busy) return
    setBusy(true)
    try {
      upsert(await api.coding.send(c.id, message))
      setText('')
    } catch (e) { toast(`Follow-up: ${(e as Error).message}`, 'error') } finally { setBusy(false) }
  }
  return (
    <form className="cs-followup" onSubmit={(e) => { e.preventDefault(); void send() }}>
      <input value={text} disabled={busy} placeholder="Send a follow-up…" aria-label={`Follow-up for ${c.name}`} onChange={(e) => setText(e.target.value)} />
      <button type="submit" className="ghost-btn sm" disabled={busy || !text.trim()}>Send</button>
    </form>
  )
}
