import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Bot } from '../../icons'
import SidebarToggle from '../../components/SidebarToggle'
import { nextStick } from '../../lib/stickToBottom'
import { useStore } from '../../store'
import { useClaudeCode } from './store'
import {
  canSend, formatCost, groupByFleet, isLive, isStoppable, pathBase, statusDot, statusLabel, summarize, summarizeFleet,
  type CcEvent, type CcSession, type FleetSummary
} from './lib'

const NONE: CcEvent[] = []
const FLEET_STATES = ['waiting_approval', 'running', 'starting', 'queued', 'done', 'failed', 'stopped'] as const

export function Counts({ sum }: { sum: FleetSummary }): JSX.Element {
  const parts = FLEET_STATES.filter((k) => sum.counts[k]).map((k) => `${sum.counts[k]} ${statusLabel(k)}`)
  return <>{parts.join(' · ') || 'no sessions'}</>
}

function EventRow({ e }: { e: CcEvent }): JSX.Element | null {
  const flat = e.text.replace(/\s+/g, ' ').trim()
  if (e.kind === 'tool') return <div className="cs-ev cs-ev-tool" title={e.text}><span className="cs-ev-name">{e.tool ?? 'tool'}</span><span className="cs-ev-sum">{flat}</span></div>
  if (e.kind === 'tool_result') return flat ? <div className="cs-ev cs-ev-result" title={e.text}>{flat}</div> : null
  if (e.kind === 'error') return <div className="cs-ev cs-ev-error">{e.text}</div>
  if (e.kind === 'approval') return <div className="cs-ev cs-ev-status"><span>{flat || 'approval requested'}</span></div>
  if (e.kind === 'init' || e.kind === 'result' || e.kind === 'note') return <div className="cs-ev cs-ev-result" title={e.text}>{flat}</div>
  return <div className="cs-ev cs-ev-text">{e.text}</div>
}

/** The whole timeline: backfilled once, appended live, and following the bottom only while the user is near it. */
function Timeline({ id }: { id: string }): JSX.Element {
  const events = useClaudeCode((s) => s.events[id]) ?? NONE
  const load = useClaudeCode((s) => s.loadEvents)
  useEffect(() => { void load(id) }, [id, load])
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
    <div className="cs-timeline" ref={ref} onScroll={onScroll} role="log" aria-label="Session activity">
      {events.length ? events.map((e) => <EventRow key={e.seq} e={e} />) : <div className="cs-ev cs-ev-result">No activity yet.</div>}
    </div>
  )
}

function SendBox({ c }: { c: CcSession }): JSX.Element | null {
  const send = useClaudeCode((s) => s.send)
  const toast = useStore((s) => s.toast)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  if (!canSend(c.status)) return null
  const go = async (): Promise<void> => {
    const message = text.trim()
    if (!message || busy) return
    setBusy(true)
    try { await send(c.id, message); setText('') } catch (e) { toast(`Send: ${(e as Error).message}`, 'error') } finally { setBusy(false) }
  }
  return (
    <form className="cs-followup" onSubmit={(e) => { e.preventDefault(); void go() }}>
      <input value={text} disabled={busy} placeholder="Send a message…" aria-label={`Message for ${c.name}`} onChange={(e) => setText(e.target.value)} />
      <button type="submit" className="ghost-btn sm" disabled={busy || !text.trim()}>Send</button>
    </form>
  )
}

function Row({ c }: { c: CcSession }): JSX.Element {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const stop = useClaudeCode((s) => s.stop)
  const toast = useStore((s) => s.toast)
  const live = isLive(c.status)
  const where = c.branch || pathBase(c.worktree)
  const sub = [statusLabel(c.status), pathBase(c.repo_path) || null, where || null, formatCost(c.cost_usd), c.num_turns != null ? `${c.num_turns} turn${c.num_turns === 1 ? '' : 's'}` : null, c.model || null]
    .filter(Boolean).join(' · ')
  const onStop = async (): Promise<void> => {
    setBusy(true)
    try { await stop(c.id) } catch (e) { toast(`Stop: ${(e as Error).message}`, 'error') } finally { setBusy(false) }
  }
  return (
    <li className={`cs-row${live ? '' : ' done'}`}>
      <div className="cs-head">
        <button className="cs-title" aria-expanded={open} title={c.worktree ?? undefined} onClick={() => setOpen((o) => !o)}>
          <span className={`inbox-dot ${statusDot(c.status)}`} role="img" aria-label={statusLabel(c.status)} title={statusLabel(c.status)} />
          <span className="cs-name">{c.name}</span>
        </button>
        {isStoppable(c.status) && <button className="ghost-btn xs" disabled={busy} onClick={() => void onStop()}>Stop</button>}
      </div>
      <small className="muted cs-sub" title={sub}>{sub}</small>
      {c.pending_approval && (
        <div className="cs-needs">
          <span className="cs-needs-text">Waiting for approval: {c.pending_approval.tool}{c.pending_approval.summary ? ` (${c.pending_approval.summary})` : ''}. Answer it on the approval card in the chat.</span>
        </div>
      )}
      {c.last_text && !open && <div className="cs-latest" title={c.last_text}>{c.last_text.replace(/\s+/g, ' ').trim()}</div>}
      {open && (
        <>
          <Timeline id={c.id} />
          <SendBox c={c} />
        </>
      )}
    </li>
  )
}

export default function ClaudeCodeView(): JSX.Element {
  const sessions = useClaudeCode((s) => s.sessions)
  const fleets = useClaudeCode((s) => s.fleets)
  const refresh = useClaudeCode((s) => s.refresh)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { refresh().then(() => setError(null)).catch((e: Error) => setError(e.message)) }, [refresh])
  const list = useMemo(() => Object.values(sessions), [sessions])
  const total = useMemo(() => summarize(list), [list])
  const groups = useMemo(() => groupByFleet(list), [list])
  const fleetById = useMemo(() => new Map(fleets.map((f) => [f.id, f])), [fleets])
  return (
    <main className="page">
      <header className="page-header drag">
        <SidebarToggle />
        <h2><Bot size={16} /> Claude Code</h2>
      </header>
      <div className="page-body" style={{ padding: '0 16px 16px' }}>
        {error && <p className="set-help" role="status">{error}</p>}
        <div className="set-help" role="status" aria-label="Fleet summary" style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', margin: '8px 0' }}>
          <strong>{formatCost(total.cost)}</strong>
          <span><Counts sum={total} /></span>
        </div>
        {!list.length && !error && <p className="set-help">No sessions yet. Ask in a chat to run Claude Code on a repo and they appear here.</p>}
        {groups.map((g) => {
          const fleet = g.fleet ? fleetById.get(g.fleet) : undefined
          const sum = fleet ? summarizeFleet(fleet, sessions) : summarize(g.sessions)
          return (
            <section key={g.fleet ?? 'solo'} aria-label={g.fleet ? `Fleet ${g.fleet}` : 'Single sessions'}>
              {(g.fleet || groups.length > 1) && (
                <h4 className="muted" style={{ margin: '12px 0 2px', fontSize: 12 }}>
                  {g.fleet ? `Fleet ${g.fleet.slice(0, 8)}` : 'Single sessions'} · {formatCost(sum.cost)} · <Counts sum={sum} />
                </h4>
              )}
              <ul className="cs-list">{g.sessions.map((c) => <Row key={c.id} c={c} />)}</ul>
            </section>
          )
        })}
      </div>
    </main>
  )
}
