import { useEffect, useState } from 'react'
import { AGENT_LABEL, codingDot, codingLive, codingStatusLabel, codingStoppable, eventSummary, formatElapsed, latestActivity, pathBase, splitSessions } from '../lib/codingSessions'
import { useStore } from '../store'
import { useTermAvailable } from '../lib/termRegistry'
import { CodingFollowUp, CodingTimeline } from './CodingTimeline'
import { CodingDiffView, NeedsYou, useStop } from './toolcards/CodingSessionCard'
import type { CodingSession } from '@shared/types'

/** Re-render every `ms` while `on`, so a running session's elapsed time moves. */
function useNow(on: boolean, ms = 10_000): number {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    if (!on) return
    setNow(Date.now() / 1000)
    const t = setInterval(() => setNow(Date.now() / 1000), ms)
    return () => clearInterval(t)
  }, [on, ms])
  return now
}

function Row({ c, now }: { c: CodingSession; now: number }): JSX.Element {
  const [open, setOpen] = useState(false)
  const [diff, setDiff] = useState(false)
  const { stop, busy } = useStop(c.id)
  const openTerminal = useStore((s) => s.openTerminal)
  const termOk = useTermAvailable()
  const events = useStore((s) => s.codingEvents[c.id])
  const live = codingLive(c.status)
  const activity = latestActivity(events)
  const line = live && activity ? eventSummary(activity) : c.detail
  const where = c.branch || pathBase(c.worktree)
  const elapsed = formatElapsed(c.created_at, live ? Math.max(now, c.created_at) : c.ended_at ?? c.updated_at)
  return (
    <li className="cs-row">
      <div className="cs-head">
        <button className="link" aria-expanded={open} title={c.worktree} onClick={() => setOpen((o) => !o)}>
          <span className={`inbox-dot ${codingDot(c.status)}`} role="img" aria-label={codingStatusLabel(c.status)} title={codingStatusLabel(c.status)} />
          <span className="cs-name">{c.name}</span>
        </button>
        {codingStoppable(c.status) && <button className="link" disabled={busy} onClick={() => void stop()}>Stop</button>}
        {termOk && <button className="link" onClick={() => openTerminal(c.id, null)}>Terminal</button>}
      </div>
      <small className="muted cs-sub">{AGENT_LABEL[c.agent]} · {codingStatusLabel(c.status)}{where ? ` · ${where}` : ''} · {elapsed}</small>
      {line && <div className="cs-latest" title={line}>{line}</div>}
      {c.status === 'needs_you' && !(c.agent === 'claude' && c.external_id) && c.attach_hint && <small className="muted"> run <code>{c.attach_hint}</code></small>}
      <NeedsYou c={c} />
      {open && (
        <>
          <CodingTimeline id={c.id} />
          <div className="cs-actions">
            <button className="link" aria-expanded={diff} onClick={() => setDiff((d) => !d)}>{diff ? 'Hide diff' : 'View diff'}</button>
          </div>
          {diff && <CodingDiffView id={c.id} />}
          <CodingFollowUp c={c} />
        </>
      )}
    </li>
  )
}

/**
 * The coding agents panel: every Claude Code / OpenCode / pi session the agent started (coding_session_* tools).
 * Live ones come first, then the ten most recent finished ones under a collapsible "Recent". A row shows status,
 * harness, branch, elapsed time and the latest activity; opening it shows the live timeline, the diff and, once
 * the session is done or stopped, a follow-up box. The store keeps rows and timelines live from the event
 * stream; the refetch here (on mount, then every 30 s while any session is live) is only a fallback.
 */
export default function CodingSessions(): JSX.Element | null {
  const sessions = useStore((s) => s.codingSessions)
  const refresh = useStore((s) => s.refreshCodingSessions)
  const [recentOpen, setRecentOpen] = useState(true)
  useEffect(() => { void refresh() }, [refresh])
  const { live, recent } = splitSessions(Object.values(sessions))
  const running = live.some((c) => codingLive(c.status))
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => void refresh(), 30_000)
    return () => clearInterval(t)
  }, [running, refresh])
  const now = useNow(running)
  if (!live.length && !recent.length) return null
  return (
    <section className="ctx-section">
      <h4>Coding sessions{running ? ` (${live.filter((c) => codingLive(c.status)).length})` : ''}</h4>
      {live.length > 0 && <ul className="plain-list">{live.map((c) => <Row key={c.id} c={c} now={now} />)}</ul>}
      {recent.length > 0 && (
        <>
          <button className="link cs-recent-toggle" aria-expanded={recentOpen} onClick={() => setRecentOpen((o) => !o)}>
            {recentOpen ? 'Hide' : 'Show'} recent ({recent.length})
          </button>
          {recentOpen && <ul className="plain-list">{recent.map((c) => <Row key={c.id} c={c} now={now} />)}</ul>}
        </>
      )}
    </section>
  )
}
