import { useEffect, useState } from 'react'
import { AGENT_LABEL, codingDot, codingLive, codingStatusLabel, codingStoppable } from '../lib/codingSessions'
import { useStore } from '../store'
import { useStop } from './toolcards/CodingSessionCard'
import type { CodingSession } from '@shared/types'

function Row({ c }: { c: CodingSession }): JSX.Element {
  const [open, setOpen] = useState(false)
  const { stop, busy } = useStop(c.id)
  return (
    <li>
      <div className="ctx-meta">
        <button className="link" aria-expanded={open} title={c.worktree} onClick={() => setOpen((o) => !o)}>
          <span className={`inbox-dot ${codingDot(c.status)}`} role="img" aria-label={codingStatusLabel(c.status)} title={codingStatusLabel(c.status)} /> {c.name}
        </button>
        {codingStoppable(c.status) && <button className="link" disabled={busy} onClick={() => void stop()}>Stop</button>}
      </div>
      <small className="muted">{AGENT_LABEL[c.agent]} · {codingStatusLabel(c.status)}{c.detail ? ` · ${c.detail}` : ''}</small>
      {c.status === 'needs_you' && c.attach_hint && <small className="muted"> · run <code>{c.attach_hint}</code></small>}
      {open && <pre className="ctx-prompt">{c.log_tail || '(no output yet)'}</pre>}
    </li>
  )
}

/**
 * Background Claude Code / OpenCode sessions the agent started (coding_session_* tools), listed beside the running
 * shell commands: attention dot, name, agent, current step, Stop, and the log tail. The store keeps the rows live from
 * the `coding_session` event; this also refetches on mount and every 5 s while any session is live, because a
 * Claude Code session moves in its own process and the backend only re-reads its files when asked. Hidden when empty.
 */
export default function CodingSessions(): JSX.Element | null {
  const sessions = useStore((s) => s.codingSessions)
  const refresh = useStore((s) => s.refreshCodingSessions)
  useEffect(() => { void refresh() }, [refresh])
  const rows = Object.values(sessions).sort((a, b) => b.created_at - a.created_at).slice(0, 10)
  const live = rows.filter((c) => codingLive(c.status)).length
  useEffect(() => {
    if (!live) return
    const t = setInterval(() => void refresh(), 5000)
    return () => clearInterval(t)
  }, [live, refresh])
  if (!rows.length) return null
  return (
    <section className="ctx-section">
      <h4>Coding sessions{live ? ` (${live})` : ''}</h4>
      <ul className="plain-list">{rows.map((c) => <Row key={c.id} c={c} />)}</ul>
    </section>
  )
}
