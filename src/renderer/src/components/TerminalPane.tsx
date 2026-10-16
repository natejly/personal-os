import { useStore } from '../store'
import { AGENT_LABEL, canOpen, codingDot, codingStatusLabel, defaultTermMode, termSessions } from '../lib/codingSessions'
import { endEntry, useTermEntries, type TermMode } from '../lib/termRegistry'
import { ageLabel } from '../lib/runningViews'
import type { CodingSession } from '@shared/types'
import ShellJobs from './ShellJobs'

/** The mode a click on a session row attaches: the attachable view, else a shell in its worktree. */
export const modeFor = (c: CodingSession): TermMode => defaultTermMode(c) ?? 'shell'

/**
 * The Terminal card: one compact row per session (running shell jobs, then the chat's coding sessions). Clicking a coding
 * session attaches it in the large TerminalWindow; the xterm views themselves live in lib/termRegistry and are never mounted here.
 */
export default function TerminalPane({ conversationId, attached, onAttach }: { conversationId?: string; attached: { id: string; mode: TermMode } | null; onAttach: (id: string, mode: TermMode) => void }): JSX.Element {
  const sessions = useStore((s) => s.codingSessions)
  const requested = useStore((s) => s.terminalRequest?.sessionId)
  const rows = termSessions(Object.values(sessions), conversationId, requested)
  const entries = useTermEntries()
  const now = Date.now() / 1000
  return (
    <div className="term-list" data-testid="term-pane">
      <ShellJobs />
      {rows.map((c) => (
        <div key={c.id} className={`term-row${attached?.id === c.id ? ' on' : ''}`} data-testid={`term-row-${c.id}`}>
          <button type="button" className="term-row-main" title={c.worktree} onClick={() => onAttach(c.id, modeFor(c))}>
            <span className={`inbox-dot ${codingDot(c.status)}`} role="img" aria-label={codingStatusLabel(c.status)} />
            <span className="term-row-name">{AGENT_LABEL[c.agent]} · {c.branch ?? c.name}</span>
            <span className="term-row-sub">{ageLabel(c.created_at, c.ended_at ?? now)}</span>
          </button>
          <span className="term-row-actions">
            {canOpen(c) && <button type="button" className="link small" data-testid="term-opencode" onClick={() => onAttach(c.id, 'opencode')}>Open in OpenCode</button>}
            {defaultTermMode(c) && <button type="button" className="link small" data-testid="term-shell" onClick={() => onAttach(c.id, 'shell')}>Shell in worktree</button>}
            {entries.some((e) => e.sessionId === c.id) && <button type="button" className="link small" data-testid="term-end" onClick={() => entries.filter((e) => e.sessionId === c.id).forEach(endEntry)}>End</button>}
          </span>
        </div>
      ))}
      {rows.length === 0 && <p className="muted small term-empty">No terminal sessions in this chat.</p>}
    </div>
  )
}
