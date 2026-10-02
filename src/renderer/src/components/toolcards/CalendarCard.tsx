import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, ArrowRight, CalendarClock, CalendarDays, CalendarPlus, CalendarSearch, CalendarX, Check, ExternalLink, Loader2, ShieldAlert, ShieldCheck, Trash2, TriangleAlert, Video, X } from 'lucide-react'
import type { CalendarEvent, ToolEvent } from '@shared/types'
import { api } from '../../lib/api'
import {
  argsFromChanges, buildOverlay, cardState, changeProblem, changesFromArgs, dayHeading, findConflicts, groupByDay, notConnected,
  parseAgendaEvents, parseResult, parseSlots, rangeDays, sameChanges, slotReplyText, toDate, toInput, newPosition,
  type CardState, type Change, type Outcome
} from '../../lib/calendarOverlay'
import { blockAriaLabel, changeAt, classifyBlocks, firstChangeMin, hourRange, LEGEND_LABEL, legendKeys, outcomeFor, weekLabel, type ViewBlock } from '../../lib/calendarProposal'
import { insertIntoComposer } from '../../lib/composerInsert'
import { useStore } from '../../store'
import { dayKey, fmtMin, fmtTime } from '../CalendarWeek'
import { eventColor, primeCalendarMeta } from '../EventEditor'
import { registerToolCard, type ToolCardProps } from './registry'
import '../../styles/calcard.css'

const pretty = (v: unknown): string => {
  if (typeof v === 'string') { try { return JSON.stringify(JSON.parse(v), null, 2) } catch { return v } }
  return JSON.stringify(v, null, 2)
}

/** Edits the user made on the card ride on the event once the server records them; before that, the original. */
const argsOf = (e: ToolEvent): Record<string, unknown> => {
  const edited = (e as ToolEvent & { edited_arguments?: Record<string, unknown> | null }).edited_arguments
  return (edited && typeof edited === 'object' ? edited : e.arguments) ?? {}
}

// ------------------------------------------------------------------ small pieces
function Details({ event }: { event: ToolEvent }): JSX.Element {
  return (
    <details className="ccard-details">
      <summary>Details</summary>
      <div className="ccard-raw">
        <h6>Call</h6><pre>{pretty(event.name + ' ' + JSON.stringify(argsOf(event)))}</pre>
        {!event.pending && !event.needs_approval && (<><h6>{event.error ? 'Error' : 'Result'}</h6><pre>{pretty(event.error ?? event.result_preview)}</pre></>)}
      </div>
    </details>
  )
}

function ConnectNotice(): JSX.Element {
  const openSettings = useStore((s) => s.openSettings)
  return (
    <div className="ccard-notice" role="status">
      <AlertCircle size={14} />
      <span>Google Calendar isn&apos;t connected.</span>
      <button className="ghost-btn sm" onClick={() => openSettings('integrations')}>Connect Google</button>
    </div>
  )
}

const STATUS: Record<CardState, { label: string; cls: string }> = {
  awaiting: { label: 'needs approval', cls: 'ask' },
  running: { label: 'applying…', cls: '' },
  denied: { label: 'denied', cls: '' },
  failed: { label: 'failed', cls: 'unproven' },
  partial: { label: 'partly done', cls: 'unproven' },
  done: { label: 'done', cls: 'verified' }
}

function Verdict({ v }: { v?: string }): JSX.Element | null {
  if (!v || v === 'unchecked') return null
  const good = v === 'verified'
  return (
    <span className={`tag ${good ? 'verified' : 'unproven'}`} title={good ? 'Read back from Google Calendar and matched' : 'Google Calendar did not confirm this change; check it before retrying'}>
      {good ? <ShieldCheck size={11} /> : <ShieldAlert size={11} />} {v}
    </span>
  )
}

const OP_LABEL = { create: 'New', update: 'Edit', delete: 'Delete' } as const
const OP_ICON = { create: <CalendarPlus size={13} />, update: <CalendarClock size={13} />, delete: <CalendarX size={13} /> } as const

/** "Wed Oct 7 · 3:00 – 3:30 PM", "Wed Oct 7 · all day", or a date range. */
function when(start?: string, end?: string): string {
  if (!start) return ''
  const s = toDate(start)
  if (Number.isNaN(s.getTime())) return start
  const day = s.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
  if (start.length === 10) {
    if (!end || end === start) return `${day} · all day`
    const last = new Date(toDate(end).getTime() - 86_400_000)
    return last > s ? `${day} – ${last.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })} · all day` : `${day} · all day`
  }
  const e = end ? toDate(end) : null
  if (!e || Number.isNaN(e.getTime())) return `${day} · ${fmtTime(s)}`
  const same = e.toDateString() === s.toDateString()
  return same ? `${day} · ${fmtTime(s)} – ${fmtTime(e)}` : `${day} ${fmtTime(s)} – ${e.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })} ${fmtTime(e)}`
}

// ------------------------------------------------------------------ the calendar grid
const GRID_HOUR_PX = 48
const todayKey = (): string => dayKey(new Date())
const hourLabel = (h: number): string => (h === 0 || h === 24 ? '' : `${h % 12 || 12} ${h < 12 ? 'AM' : 'PM'}`)

interface GridProps {
  days: string[]
  blocks: ViewBlock[]
  changes: Change[]
  selected: number | null
  outcomes: Outcome[]
  label: string
  onSelect: (i: number) => void
}

function BlockBody({ b, tall }: { b: ViewBlock; tall: boolean }): JSX.Element {
  const range = b.allDay ? 'all day' : `${fmtMin(b.day, b.startMin)} – ${fmtMin(b.day, b.endMin)}`
  return (
    <>
      <b className="pg-title">
        {b.variant === 'delete' && <Trash2 size={12} />}
        {b.variant === 'done' && <Check size={12} />}
        {b.variant === 'failed' && <X size={12} />}
        {b.variant === 'moved-to' && <ArrowRight size={12} />}
        <span>{b.summary}</span>
      </b>
      {tall && <span className="pg-time">{range}{b.movedTo ? ` → ${fmtMin(b.movedTo.day, b.movedTo.startMin)}` : ''}</span>}
      {tall && b.movedFrom && <span className="pg-time">from {fmtMin(b.movedFrom.day, b.movedFrom.startMin)}</span>}
      {b.variant === 'new' && <span className="pg-tag">+ New</span>}
      {b.variant === 'edited' && <span className="pg-tag">edited</span>}
      {b.variant === 'moved-to' && !tall && <span className="pg-tag">moved</span>}
    </>
  )
}

function CalendarGrid({ days, blocks, changes, selected, outcomes, label, onSelect }: GridProps): JSX.Element {
  const band = useMemo(() => hourRange(blocks), [blocks])
  const hours = band.end - band.start
  const scroller = useRef<HTMLDivElement>(null)
  const today = todayKey()
  const shown = useMemo(() => blocks.filter((b) => days.includes(b.day)), [blocks, days])
  const first = useMemo(() => firstChangeMin(shown), [shown])
  const allDay = shown.filter((b) => b.allDay)
  const top = (min: number): number => ((min - band.start * 60) / 60) * GRID_HOUR_PX

  // Land on the first change, one hour of context above it.
  useEffect(() => {
    const el = scroller.current
    if (el && first !== null) el.scrollTop = Math.max(0, top(first) - GRID_HOUR_PX * 0.75)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [first, band.start, days.join(',')])

  const place = (b: ViewBlock): React.CSSProperties => ({
    top: top(b.startMin), height: Math.max(22, top(b.endMin) - top(b.startMin) - 2),
    left: `calc(${(b.lane / b.lanes) * 100}% + 2px)`, width: `calc(${100 / b.lanes}% - 4px)`,
    ...(b.variant === 'existing' && b.color ? { borderLeftColor: b.color } : {})
  })
  const attend = (b: ViewBlock): string[] => changeAt(changes, b.change)?.attendees ?? []
  const badge = (b: ViewBlock): JSX.Element | null => {
    const o = outcomeFor(outcomes, b.change)
    if (!o) return null
    return <span className={`pg-badge ${o.ok ? 'ok' : 'bad'}`} title={o.ok ? (o.v === 'verified' ? 'Done and verified' : `Done${o.v ? `, ${o.v}` : ''}`) : o.err || 'Not applied'}>{o.ok ? <ShieldCheck size={11} /> : <ShieldAlert size={11} />}</span>
  }

  const cls = (b: ViewBlock): string => `pg-block v-${b.variant}${b.off ? ' off' : ''}${b.conflict ? ' conflict' : ''}${b.change !== undefined && b.change === selected ? ' sel' : ''}`
  const tip = (b: ViewBlock): string => `${b.summary} · ${b.allDay ? 'all day' : `${fmtMin(b.day, b.startMin)} – ${fmtMin(b.day, b.endMin)}`}${b.conflict ? ' · conflict: overlaps another event' : ''}`

  return (
    <div className="pg" ref={scroller} role="group" aria-label={label}
      style={{ gridTemplateColumns: `46px repeat(${days.length}, minmax(0, 1fr))` }}>
      <div className="pg-corner" />
      {days.map((d) => {
        const dt = toDate(d)
        return (
          <div key={d} className={`pg-dayhead${d === today ? ' today' : ''}`}>
            <span>{dt.toLocaleDateString(undefined, { weekday: 'short' })}</span>
            <b>{dt.getDate()}</b>
          </div>
        )
      })}
      {allDay.length > 0 && <div className="pg-gutter pg-adlabel">all day</div>}
      {allDay.length > 0 && days.map((d) => (
        <div key={'ad' + d} className="pg-allday">
          {allDay.filter((b) => b.day === d).map((b) => (b.change !== undefined
            ? <button key={b.key} type="button" className={`pg-chip ${cls(b)}`} title={tip(b)} aria-label={blockAriaLabel(b, attend(b))} aria-pressed={b.change === selected} onClick={() => onSelect(b.change as number)}>{badge(b)}<span>{b.summary}</span></button>
            : <div key={b.key} className="pg-chip v-existing" title={tip(b)} style={b.color ? { borderLeftColor: b.color } : undefined}><span>{b.summary}</span></div>))}
        </div>
      ))}
      <div className="pg-gutter" style={{ height: hours * GRID_HOUR_PX }}>
        {Array.from({ length: hours }, (_, i) => band.start + i).map((h) => (
          <div key={h} className="pg-hour" style={{ height: GRID_HOUR_PX }}><span>{hourLabel(h)}</span></div>
        ))}
      </div>
      {days.map((d) => (
        <div key={d} className={`pg-col${d === today ? ' today' : ''}`} style={{ height: hours * GRID_HOUR_PX }}>
          {Array.from({ length: hours }, (_, i) => <div key={i} className="pg-line" style={{ top: i * GRID_HOUR_PX }} />)}
          {shown.filter((b) => b.day === d && !b.allDay).map((b) => {
            const tall = top(b.endMin) - top(b.startMin) >= 40
            return b.change !== undefined ? (
              <button key={b.key} type="button" className={cls(b)} style={place(b)} title={tip(b)} aria-label={blockAriaLabel(b, attend(b))}
                aria-pressed={b.change === selected} onClick={() => onSelect(b.change as number)}>
                <BlockBody b={b} tall={tall} />{badge(b)}
              </button>
            ) : (
              <div key={b.key} className={cls(b)} style={place(b)} title={tip(b)}><BlockBody b={b} tall={tall} /></div>
            )
          })}
        </div>
      ))}
    </div>
  )
}

function GridBar({ days, blocks, count }: { days: string[]; blocks: ViewBlock[]; count: number }): JSX.Element {
  const keys = legendKeys(blocks)
  return (
    <div className="pg-bar">
      <b className="pg-week">{weekLabel(days)}</b>
      <div className="pg-legend" aria-hidden="true">
        {keys.map((k) => <span key={k} className={`pg-chipkey k-${k}`}><i />{LEGEND_LABEL[k]}</span>)}
      </div>
      <span className="pg-count">{count} {count === 1 ? 'change' : 'changes'}</span>
    </div>
  )
}

// ------------------------------------------------------------------ the user's real calendar, for the timeline
const looked = new Map<string, CalendarEvent>()

function useTimelineData(changes: Change[], connected: boolean, fresh: boolean): { old: Record<string, CalendarEvent>; days: string[]; existing: CalendarEvent[] | null; failed: boolean } {
  const [old, setOld] = useState<Record<string, CalendarEvent>>({})
  const [existing, setExisting] = useState<CalendarEvent[] | null>(null)
  const [failed, setFailed] = useState(false)
  const targets = useMemo(() => changes.filter((c) => c.op !== 'create' && c.event_id).map((c) => `${c.calendar_id ?? 'primary'}\u0000${c.event_id}`), [changes])
  const targetKey = targets.join('|')

  useEffect(() => {
    if (!connected || !targets.length) return
    let dead = false
    void Promise.all(targets.map(async (t) => {
      const [cal, id] = t.split('\u0000')
      if (!looked.has(id)) {
        try { looked.set(id, await api.google.getEvent(id, cal)) } catch { /* gone, or not readable: the row falls back to what the call says */ }
      }
      return looked.get(id)
    })).then((rows) => { if (!dead) setOld(Object.fromEntries(rows.filter((e): e is CalendarEvent => !!e).map((e) => [e.id, e]))) })
    return () => { dead = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetKey, connected])

  const days = useMemo(() => rangeDays(changes, old, [], 7), [changes, old])
  const dayKey = days.join(',')

  useEffect(() => {
    if (!connected || !days.length) { setExisting([]); return }
    let dead = false
    const first = days[0], last = days[days.length - 1]
    const span = Math.min(60, Math.round((toDate(last).getTime() - toDate(first).getTime()) / 86_400_000) + 1)
    void primeCalendarMeta().then(() => api.google.calendarRange(toDate(first).toISOString(), span, 'all', fresh))
      .then((rows) => { if (!dead) { setExisting(rows); setFailed(false) } })
      .catch(() => { if (!dead) { setExisting([]); setFailed(true) } })
    return () => { dead = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dayKey, connected, fresh])

  return { old, days, existing, failed }
}

// ------------------------------------------------------------------ one editable / read-only change
interface RowProps {
  c: Change
  i: number
  editing: boolean
  on: boolean
  old?: CalendarEvent
  outcome?: Outcome
  state: CardState
  conflicts?: string[]
  onToggle: (on: boolean) => void
  onEdit: (patch: Partial<Change>) => void
}

const parseGuests = (t: string): string[] => t.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean)

function ChangeRow({ c, i, editing, on, old, outcome, state, conflicts, onToggle, onEdit }: RowProps): JSX.Element {
  const [guests, setGuests] = useState((c.attendees ?? []).join(', '))
  const allDay = (c.start ?? old?.start ?? '').length === 10
  const problem = editing && on ? changeProblem(c) : null
  const pos = c.op === 'update' ? newPosition(c, old) : { start: c.start, end: c.end }
  const title = c.summary ?? old?.summary ?? 'Event'
  const shownStart = outcome?.a ?? pos.start
  const shownEnd = outcome?.b ?? pos.end
  const moved = c.op === 'update' && !!old && !!pos.start && (toDate(pos.start).getTime() !== toDate(old.start).getTime())

  const setStart = (v: string): void => {
    const patch: Partial<Change> = { start: v }
    // Dragging the start along keeps the length, like Google's own editor.
    if (c.start && c.end && !allDay && v) patch.end = toInput(new Date(toDate(v).getTime() + (toDate(c.end).getTime() - toDate(c.start).getTime())).toISOString())
    onEdit(patch)
  }

  return (
    <li className={`ccard-row ${on ? '' : 'off'} ${state}`} data-op={c.op}>
      <div className="ccard-row-head">
        {editing ? (
          <input type="checkbox" checked={on} onChange={(e) => onToggle(e.target.checked)} aria-label={`Include change ${i + 1}: ${OP_LABEL[c.op]} ${title}`} />
        ) : (
          <span className="ccard-status" aria-hidden="true">
            {outcome ? (outcome.ok ? <Check size={13} className="ok" /> : <X size={13} className="bad" />)
              : state === 'running' ? <Loader2 size={13} className="spin" /> : null}
          </span>
        )}
        <span className={`ccard-op op-${c.op}`}>{OP_ICON[c.op]}{OP_LABEL[c.op]}</span>
        {editing && c.op !== 'delete' ? (
          <input className="ccard-title" value={c.summary ?? old?.summary ?? ''} placeholder="Title" aria-label="Title" disabled={!on}
            onChange={(e) => onEdit({ summary: e.target.value })} />
        ) : (
          <span className={`ccard-name ${c.op === 'delete' ? 'struck' : ''}`}>{title}</span>
        )}
        {!editing && outcome && <Verdict v={outcome.v} />}
        {!editing && outcome?.link && (
          <a className="ccard-open" href={outcome.link} target="_blank" rel="noreferrer" title="Open in Google Calendar"><ExternalLink size={12} /> Open in Calendar</a>
        )}
        {c.conference && <span className="tag" title="Adds a Google Meet link"><Video size={11} /> Meet</span>}
      </div>

      {editing && c.op !== 'delete' && on ? (
        <div className="ccard-edit">
          <label>Start<input type={allDay ? 'date' : 'datetime-local'} value={toInput(c.start ?? old?.start ?? '')} onChange={(e) => setStart(e.target.value)} /></label>
          <label>End<input type={allDay ? 'date' : 'datetime-local'} value={toInput(c.end ?? (c.start ? '' : old?.end ?? ''))} onChange={(e) => onEdit({ end: e.target.value })} /></label>
          <label className="wide">Guests<input value={guests} placeholder="email, email" onChange={(e) => { setGuests(e.target.value); onEdit({ attendees: parseGuests(e.target.value) }) }} /></label>
          <label className="ccard-meet"><input type="checkbox" checked={!!c.conference} onChange={(e) => onEdit({ conference: e.target.checked })} /> <Video size={12} /> Add Google Meet link</label>
        </div>
      ) : (
        <div className="ccard-when">
          {shownStart ? when(shownStart, shownEnd) : old ? when(old.start, old.end) : ''}
          {moved && old && <span className="ccard-was"> · was {when(old.start, old.end)}</span>}
          {(c.attendees?.length ?? 0) > 0 && <span className="ccard-guests"> · {c.attendees?.join(', ')}</span>}
          {c.location && <span className="ccard-guests"> · {c.location}</span>}
        </div>
      )}

      {editing && on && conflicts && conflicts.length > 0 && (
        <div className="ccard-warn"><TriangleAlert size={12} /> Overlaps {conflicts.join(', ')}</div>
      )}
      {problem && <div className="ccard-warn bad"><AlertCircle size={12} /> {problem}</div>}
      {!editing && outcome && !outcome.ok && <div className="ccard-warn bad"><AlertCircle size={12} /> {outcome.err || 'This change did not complete.'}</div>}
      {!editing && outcome && outcome.ok && outcome.v && outcome.v !== 'verified' && outcome.v !== 'unchecked' && (
        <div className="ccard-warn bad"><AlertCircle size={12} /> Google did not confirm this one. Check your calendar before retrying.</div>
      )}
    </li>
  )
}

// ------------------------------------------------------------------ the proposal / write card
function ProposalCard({ event, pending, decide }: ToolCardProps): JSX.Element {
  const name = event.name
  const state = cardState(name, event, pending)
  const original = useMemo(() => changesFromArgs(name, argsOf(event)), [name, event])
  const connected = useStore((s) => !!s.google?.connected)

  const [draft, setDraft] = useState<Change[]>(original)
  const [enabled, setEnabled] = useState<boolean[]>(() => original.map(() => true))
  const [submitted, setSubmitted] = useState<boolean[] | null>(null)
  const [busy, setBusy] = useState(false)
  const initialNotify = original.some((c) => c.send_updates === 'all')
  const [notify, setNotify] = useState(initialNotify)

  const editing = state === 'awaiting' && !submitted
  // Between "approved" and the first result the card keeps what the user approved; afterwards it
  // reads the event, whose arguments are the ones that ran, so a reload shows the same thing.
  const changes = editing ? draft : state === 'running' && submitted ? draft.filter((_, i) => submitted[i]) : original
  const parsed = useMemo(() => parseResult(name, event.result_preview, event.error), [name, event.result_preview, event.error])
  const finished = state === 'done' || state === 'partial' || state === 'failed'
  const outcomes = finished ? parsed.outcomes : []

  const { old, days, existing, failed } = useTimelineData(changes, connected, !editing)
  const conflicts = useMemo(() => (editing && existing ? findConflicts(changes, existing, { enabled, old }) : {}), [editing, changes, existing, enabled, old])
  // Switched-off changes stay on the grid, greyed, so the picture never loses what the user is declining.
  const blocks = useMemo(() => {
    const bs = buildOverlay(changes, existing ?? [], { old, outcomes: finished ? outcomes : null, colorOf: eventColor })
    return classifyBlocks(bs.map((b) => (b.change !== undefined && conflicts[b.change] ? { ...b, conflict: true } : b)), editing ? enabled : undefined)
  }, [changes, existing, enabled, editing, old, finished, outcomes, conflicts])
  const [selectedRaw, setSelected] = useState<number | null>(0)
  const [listView, setListView] = useState(false)
  const selected = selectedRaw !== null && selectedRaw < changes.length ? selectedRaw : null

  const patch = (i: number, p: Partial<Change>): void => setDraft((d) => d.map((c, n) => (n === i ? { ...c, ...p } : c)))
  const picked = draft.filter((_, i) => enabled[i])
  const blocked = picked.map(changeProblem).find(Boolean)
  const allOn = enabled.every(Boolean)
  const guests = picked.some((c) => (c.attendees?.length ?? 0) > 0)
  const note = typeof argsOf(event).note === 'string' ? (argsOf(event).note as string) : ''
  const hasGrid = existing !== null && days.length > 0
  const okCount = outcomes.filter((o) => o.ok).length
  const total = state === 'awaiting' ? picked.length : changes.length

  const approve = async (): Promise<void> => {
    if (!picked.length || blocked || busy) return
    setBusy(true)
    try {
      const out = picked.map((c) => (notify !== initialNotify && (c.attendees?.length ?? 0) > 0 ? { ...c, send_updates: notify ? 'all' : 'none' } : c))
      const edited = !sameChanges(out, original)
      setSubmitted(enabled.slice())
      // Unedited and complete goes back as a plain approval; anything else carries the exact arguments to run.
      await decide(true, edited ? argsFromChanges(name, argsOf(event), out) : undefined)
    } finally { setBusy(false) }
  }
  const deny = async (): Promise<void> => { if (!busy) { setBusy(true); try { await decide(false) } finally { setBusy(false) } } }

  const title = name === 'calendar_propose' ? 'Calendar changes' : OP_LABEL[changes[0]?.op ?? 'create'] === 'New' ? 'New event' : changes[0]?.op === 'delete' ? 'Delete event' : 'Edit event'
  const st = state === 'partial' ? { label: `${okCount} of ${changes.length} done`, cls: 'unproven' } : state === 'done' && okCount > 0 ? { label: changes.length > 1 ? 'all done' : 'done', cls: 'verified' } : STATUS[state]

  return (
    <section className={`ccard ${state}`} aria-label={`${title}, ${st.label}`} onKeyDown={(e) => { if (editing && (e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); void approve() } }}>
      <header className="ccard-head">
        <CalendarDays size={14} />
        <b>{title}</b>
        {!hasGrid && <span className="ccard-count">{total} {total === 1 ? 'change' : 'changes'}</span>}
        <span className={`tag ${st.cls}`}>{st.label}</span>
      </header>
      {note && <p className="ccard-note">{note}</p>}
      {(notConnected(event.error) || (!connected && (editing || state === 'failed'))) && <ConnectNotice />}

      {connected && existing === null && days.length > 0 && <div className="ccard-loading"><Loader2 size={12} className="spin" /> Loading your calendar…</div>}
      {/* Without Google there are no existing events (existing = []), but the proposed changes still get the grid. */}
      {hasGrid && (
        <>
          <GridBar days={days} blocks={blocks} count={total} />
          <CalendarGrid days={days} blocks={blocks} changes={changes} selected={editing ? selected : null} outcomes={outcomes} onSelect={setSelected}
            label={`Calendar preview of ${days.length} ${days.length === 1 ? 'day' : 'days'} with ${changes.length} ${changes.length === 1 ? 'change' : 'changes'}`} />
          {failed && <p className="ccard-muted">Couldn&apos;t load your existing events, so conflicts are not checked.</p>}
        </>
      )}

      {editing && hasGrid && changes.length > 0 && (
        <div className="pg-pick" role="group" aria-label="Changes">
          {changes.map((c, i) => (
            <button key={i} type="button" className={`pg-pickbtn op-${c.op}${i === selected ? ' sel' : ''}${enabled[i] === false ? ' off' : ''}`} aria-pressed={i === selected} onClick={() => setSelected(i)}>
              {OP_ICON[c.op]}<span>{c.summary ?? old[c.event_id ?? '']?.summary ?? OP_LABEL[c.op]}</span>
            </button>
          ))}
          <button type="button" className="ghost-btn sm pg-listtoggle" aria-pressed={listView} onClick={() => setListView((v) => !v)}>{listView ? 'Hide list' : 'List view'}</button>
        </div>
      )}

      {(() => {
        // Editing with a grid: one compact panel for the picked change (or all rows via List view). Otherwise the full list, which carries the results.
        const only = editing && hasGrid && !listView
        const rows = only ? (selected !== null ? [selected] : []) : changes.map((_, i) => i)
        return (
          <ul className={`ccard-list${only ? ' panel' : ''}`}>
            {rows.map((i) => {
              const c = changes[i]
              return (
                <ChangeRow key={i} c={c} i={i} editing={editing} on={editing ? enabled[i] !== false : true} state={state}
                  old={c.event_id ? old[c.event_id] : undefined}
                  outcome={outcomes.find((o) => o.i === i)} conflicts={conflicts[i]}
                  onToggle={(on) => setEnabled((en) => en.map((v, n) => (n === i ? on : v)))} onEdit={(p) => patch(i, p)} />
              )
            })}
          </ul>
        )
      })()}
      {finished && parsed.truncated && <p className="ccard-muted">The saved result was cut short, so some changes show no outcome. Open the calendar to check them.</p>}
      {state === 'failed' && !outcomes.length && event.error && !notConnected(event.error) && <div className="ccard-warn bad"><AlertCircle size={12} /> {event.error}</div>}
      {state === 'denied' && <p className="ccard-muted">You declined this. Nothing was changed.</p>}

      {editing && (
        <footer className="ccard-foot">
          {guests && (
            <label className="ccard-notify"><input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} /> Email guests about these changes</label>
          )}
          {blocked && <span className="ccard-warn bad"><AlertCircle size={12} /> {blocked}</span>}
          <div className="ccard-actions">
            <button className="primary-btn" disabled={busy || !picked.length || !!blocked} onClick={() => void approve()}
              title="⌘↵">{allOn ? (changes.length > 1 ? `Approve all (${picked.length})` : 'Approve') : `Approve selected (${picked.length})`}</button>
            <button className="ghost-btn danger" disabled={busy} onClick={() => void deny()}>Deny</button>
          </div>
        </footer>
      )}
      <Details event={event} />
    </section>
  )
}

// ------------------------------------------------------------------ read-only results: slots and events
function AwaitingRead({ decide }: Pick<ToolCardProps, 'decide'>): JSX.Element {
  const [busy, setBusy] = useState(false)
  return (
    <div className="ccard-foot">
      <p className="ccard-muted">This tool is set to ask first. It only reads your calendar.</p>
      <div className="ccard-actions">
        <button className="primary-btn" disabled={busy} onClick={() => { setBusy(true); void decide(true).finally(() => setBusy(false)) }}>Allow</button>
        <button className="ghost-btn danger" disabled={busy} onClick={() => { setBusy(true); void decide(false).finally(() => setBusy(false)) }}>Deny</button>
      </div>
    </div>
  )
}

function SlotsView({ event }: { event: ToolEvent }): JSX.Element {
  const r = useMemo(() => parseResult(event.name, event.result_preview, event.error), [event])
  const slots = parseSlots(r.data)
  const args = event.arguments as { duration_minutes?: number; attendees?: string[] }
  const groups = groupByDay(slots.map((s) => ({ ...s, day: s.start.slice(0, 10) })))
  const note = typeof r.data?.note === 'string' ? r.data.note : ''
  return (
    <>
      <p className="ccard-sub">
        {args.duration_minutes ? `${args.duration_minutes} min` : 'Free times'}
        {args.attendees && args.attendees.length > 0 && ` with ${args.attendees.join(', ')}`}
        {slots.length > 0 && ' · tap a time to reply with it'}
      </p>
      {groups.map((g) => (
        <div key={g.day} className="ccard-agday">
          <div className="ccard-aghead">{dayHeading(g.day)}</div>
          <div className="ccard-slots">
            {g.items.map((s) => (
              <button key={s.start} className="ccard-slot" title={slotReplyText(s.start, s.end)}
                onClick={() => insertIntoComposer(slotReplyText(s.start, s.end))}>
                {fmtTime(toDate(s.start))} – {fmtTime(toDate(s.end))}
              </button>
            ))}
          </div>
        </div>
      ))}
      {!slots.length && <p className="ccard-muted">{note || 'No free slot fits that window.'}</p>}
      {slots.length > 0 && note && <div className="ccard-warn"><TriangleAlert size={12} /> {note}</div>}
    </>
  )
}

function EventsView({ event }: { event: ToolEvent }): JSX.Element {
  const r = useMemo(() => parseResult(event.name, event.result_preview, event.error), [event])
  const events = parseAgendaEvents(r.data)
  const total = typeof r.data?.total === 'number' ? r.data.total : events.length
  const items = events.map((e) => ({ e, day: e.all_day ? e.start : toDate(e.start).toLocaleDateString('en-CA') }))
  const groups = groupByDay(items)
  return (
    <>
      {groups.map((g) => (
        <div key={g.day} className="ccard-agday">
          <div className="ccard-aghead">{dayHeading(g.day)}</div>
          <ul className="ccard-ag">
            {g.items.sort((a, b) => Number(b.e.all_day) - Number(a.e.all_day)).map(({ e }) => (
              <li key={e.id + e.start}>
                <span className="ccard-agtime">{e.all_day ? 'all day' : `${fmtTime(toDate(e.start))}`}</span>
                <span className="ccard-agtitle">{e.summary}</span>
                {e.location && <span className="ccard-guests">{e.location}</span>}
                {e.link && <a className="ccard-open" href={e.link} target="_blank" rel="noreferrer" aria-label={`Open ${e.summary} in Google Calendar`}><ExternalLink size={11} /></a>}
              </li>
            ))}
          </ul>
        </div>
      ))}
      {!events.length && <p className="ccard-muted">{r.data ? 'Nothing scheduled in that range.' : 'No events to show.'}</p>}
      {total > events.length && <p className="ccard-muted">+{total - events.length} more not shown</p>}
    </>
  )
}

function ReadCard({ event, pending, decide }: ToolCardProps): JSX.Element {
  const state = cardState(event.name, event, pending)
  const isSlots = event.name === 'calendar_find_time'
  const miss = notConnected(event.error)
  const Icon = isSlots ? CalendarSearch : CalendarDays
  const label = isSlots ? 'Free times' : 'Calendar'
  return (
    <section className={`ccard read ${state}`} aria-label={label}>
      <header className="ccard-head">
        <Icon size={14} /><b>{label}</b>
        {state === 'running' && <span className="tag"><Loader2 size={11} className="spin" /> looking…</span>}
        {state === 'awaiting' && <span className="tag ask">needs approval</span>}
        {state === 'denied' && <span className="tag">denied</span>}
        {state === 'failed' && <span className="tag unproven">failed</span>}
      </header>
      {miss ? <ConnectNotice /> : state === 'failed' ? <div className="ccard-warn bad"><AlertCircle size={12} /> {event.error}</div> : null}
      {state === 'awaiting' && <AwaitingRead decide={decide} />}
      {state === 'done' && (isSlots ? <SlotsView event={event} /> : <EventsView event={event} />)}
      <Details event={event} />
    </section>
  )
}

export default function CalendarCard(props: ToolCardProps): JSX.Element {
  return ['calendar_find_time', 'calendar_events'].includes(props.event.name) ? <ReadCard {...props} /> : <ProposalCard {...props} />
}

for (const n of ['calendar_propose', 'calendar_create', 'calendar_update', 'calendar_delete', 'calendar_find_time', 'calendar_events']) registerToolCard(n, CalendarCard)
