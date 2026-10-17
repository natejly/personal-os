import { useEffect, useState } from 'react'
import { useCoding } from './store'
import { AGENT_LABEL, canOpen, codingDot, codingStatusLabel, defaultTermMode, termSessions } from './lib/codingSessions'
import { endEntry, useTermEntries, type TermMode } from './termRegistry'
import { ageLabel } from '../../lib/runningViews'
import { api } from '../../lib/api'
import type { AgentTerminal, CodingSession } from '@shared/types'
import ShellJobs from '../../components/ShellJobs'

/** The mode a click on a session row attaches: the attachable view, else a shell in its worktree. */
export const modeFor = (c: CodingSession): TermMode => defaultTermMode(c) ?? 'shell'

/** The terminals agents have open, listed while the card is open and refreshed when it is shown again. */
function useAgentTerminals(): AgentTerminal[] {
  const [rows, setRows] = useState<AgentTerminal[]>([])
  useEffect(() => {
    let live = true
    const load = (): void => { void api.coding.terminals().then((r) => { if (live) setRows(r.terminals) }).catch(() => undefined) }
    load()
    const t = setInterval(load, 5000)
    return () => { live = false; clearInterval(t) }
  }, [])
  return rows
}

/**
 * The Terminal card: one compact row per session (running shell jobs, then the chat's coding sessions, then the shells
 * agents opened). Clicking a coding session or an agent terminal attaches it in the large TerminalWindow; the xterm views
 * themselves live in termRegistry and are never mounted here.
 */
export default function TerminalPane({ conversationId }: { conversationId?: string }): JSX.Element {
  const sessions = useCoding((s) => s.sessions)
  const requested = useCoding((s) => s.terminalRequest?.sessionId)
  const attached = useCoding((s) => s.attached)
  const attach = useCoding((s) => s.attach)
  const rows = termSessions(Object.values(sessions), conversationId, requested)
  const entries = useTermEntries()
  const agentTerminals = useAgentTerminals()
  const now = Date.now() / 1000
  const termsOf = (sessionId: string | null): AgentTerminal[] => agentTerminals.filter((t) => t.session_id === sessionId)
  const terminalRow = (t: AgentTerminal): JSX.Element => (
    <div key={t.id} className={`term-row${attached?.id === t.id ? ' on' : ''}`} data-testid={`term-agent-${t.id}`}>
      <button type="button" className="term-row-main" title={t.cwd} onClick={() => attach(t.id, 'agent')}>
        <span className="inbox-dot working" role="img" aria-label="open" />
        <span className="term-row-name">{t.name || 'Agent terminal'}{t.user_attached ? ' · yours' : ''}</span>
        <span className="term-row-sub">sandboxed · {ageLabel(t.created_at, now)}</span>
      </button>
    </div>
  )
  return (
    <div className="term-list" data-testid="term-pane">
      <ShellJobs />
      {rows.map((c) => (
        <div key={c.id}>
          <div className={`term-row${attached?.id === c.id ? ' on' : ''}`} data-testid={`term-row-${c.id}`}>
            <button type="button" className="term-row-main" title={c.worktree} onClick={() => attach(c.id, modeFor(c))}>
              <span className={`inbox-dot ${codingDot(c.status)}`} role="img" aria-label={codingStatusLabel(c.status)} />
              <span className="term-row-name">{AGENT_LABEL[c.agent]} · {c.branch ?? c.name}</span>
              <span className="term-row-sub">{ageLabel(c.created_at, c.ended_at ?? now)}</span>
            </button>
            <span className="term-row-actions">
              {canOpen(c) && <button type="button" className="link small" data-testid="term-opencode" onClick={() => attach(c.id, 'opencode')}>Open in OpenCode</button>}
              {defaultTermMode(c) && <button type="button" className="link small" data-testid="term-shell" onClick={() => attach(c.id, 'shell')}>Shell in worktree</button>}
              {entries.some((e) => e.sessionId === c.id) && <button type="button" className="link small" data-testid="term-end" onClick={() => entries.filter((e) => e.sessionId === c.id).forEach(endEntry)}>End</button>}
            </span>
          </div>
          {termsOf(c.id).map(terminalRow)}
        </div>
      ))}
      {termsOf(null).length > 0 && <h4 className="term-group">Agent terminals</h4>}
      {termsOf(null).map(terminalRow)}
      {rows.length === 0 && agentTerminals.length === 0 && <p className="muted small term-empty">No terminal sessions in this chat.</p>}
    </div>
  )
}
