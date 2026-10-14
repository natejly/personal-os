import { useEffect, useRef, useState } from 'react'
import '@xterm/xterm/css/xterm.css'
import { useStore } from '../store'
import { AGENT_LABEL, canAttach, canOpen, codingDot, codingLive, codingStatusLabel, defaultTermMode, defaultTermSession, termSessions } from '../lib/codingSessions'
import { endEntry, ensureEntry, getEntry, refit, startEntry, useTermEntries, type TermMode } from '../lib/termRegistry'
import { CodingTimeline } from './CodingTimeline'
import ShellJobs from './ShellJobs'

// The last openTerminal request this pane acted on, so remounting the pane does not replay it.
let handled = 0
// The session and view on screen when the pane last unmounted, so coming back from another tab shows what you left.
let last: { id: string; mode: TermMode | null } | null = null

/**
 * Terminals for coding sessions: opening the tab picks the newest attachable session and picking a session attaches to it right
 * away when it can (claude with an attach id); a shell in its worktree is an explicit button and the only view for sessions that cannot attach. The xterm views live
 * in lib/termRegistry, not in this component, so closing the panel or switching tab only detaches their DOM; the PTYs and
 * their screens are still there when it comes back.
 */
export default function TerminalPane({ conversationId }: { conversationId?: string }): JSX.Element {
  const sessions = useStore((s) => s.codingSessions)
  const request = useStore((s) => s.terminalRequest)
  const rows = termSessions(Object.values(sessions), conversationId, request?.sessionId)
  const kept = last && rows.some((c) => c.id === last!.id) ? last : null
  const [sessionId, setSessionId] = useState<string | null>(request?.sessionId ?? kept?.id ?? defaultTermSession(rows)?.id ?? null)
  const [mode, setMode] = useState<TermMode | null>(kept?.mode ?? null)
  useEffect(() => { if (sessionId) last = { id: sessionId, mode } }, [sessionId, mode])
  const entries = useTermEntries()
  const viewRef = useRef<HTMLDivElement>(null)
  const selected = rows.find((c) => c.id === sessionId) ?? null

  const start = (id: string, m: TermMode): void => {
    setSessionId(id); setMode(m)
    void startEntry(ensureEntry(id, m))
  }

  // Picking a session shows its attached view at once when it has one; otherwise whichever view already exists.
  const pick = (id: string): void => {
    const c = sessions[id]
    if (c && defaultTermMode(c)) start(id, 'attach')
    else { setSessionId(id); setMode(null) }
  }

  // No fresh request and nothing left open: attach to the default session once, as soon as there is one (sessions can arrive after mount).
  const auto = useRef(!!kept)
  useEffect(() => {
    if (auto.current) return
    if (request && request.n !== handled) { auto.current = true; return }
    const d = defaultTermSession(rows)
    if (!d) return
    auto.current = true
    pick(d.id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows.length])

  useEffect(() => {
    if (!request || request.n === handled) return
    handled = request.n
    if (request.mode) start(request.sessionId, request.mode)
    else pick(request.sessionId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request])

  // The mode on screen: the one picked, else whichever view of this session already exists.
  const have = (m: TermMode): boolean => !!sessionId && entries.some((e) => e.sessionId === sessionId && e.mode === m)
  const shownMode: TermMode | null = mode && have(mode) ? mode : have('attach') ? 'attach' : have('opencode') ? 'opencode' : have('shell') ? 'shell' : null
  const shown = sessionId && shownMode ? getEntry(sessionId, shownMode) ?? null : null

  // Mount the shown view's DOM; every other one stays detached but alive in the registry.
  useEffect(() => {
    const box = viewRef.current
    if (!box || !shown) return
    for (const h of Array.from(box.children)) if (h !== shown.host) h.remove()
    if (shown.host.parentElement !== box) box.appendChild(shown.host)
    let timer: ReturnType<typeof setTimeout> | undefined
    const ro = new ResizeObserver(() => { clearTimeout(timer); timer = setTimeout(() => refit(shown), 100) })
    ro.observe(box)
    const raf = requestAnimationFrame(() => { refit(shown); shown.term.focus() })
    return () => { ro.disconnect(); clearTimeout(timer); cancelAnimationFrame(raf); if (shown.host.parentElement === box) shown.host.remove() }
  }, [shown])

  return (
    <div className="desk-pane term-pane" data-testid="term-pane">
      <div className="term-jobs"><ShellJobs /></div>
      {rows.length > 0 && <div className="term-chips">
        {rows.map((c) => (
          <button key={c.id} type="button" className={`term-chip${c.id === sessionId ? ' on' : ''}`} data-testid={`term-chip-${c.id}`} title={c.worktree}
            onClick={() => pick(c.id)}>
            <span className={`inbox-dot ${codingDot(c.status)}`} role="img" aria-label={codingStatusLabel(c.status)} />
            <span>{AGENT_LABEL[c.agent]}</span>
            <span className="term-chip-branch">{c.branch ?? c.name}</span>
          </button>
        ))}
      </div>}
      {selected && (
        <div className="term-actions">
          {canAttach(selected) && <button type="button" className="ghost-btn sm" data-testid="term-attach" onClick={() => start(selected.id, 'attach')}>Attach</button>}
          {canOpen(selected) && <button type="button" className="ghost-btn sm" data-testid="term-opencode" onClick={() => start(selected.id, 'opencode')}>Open in OpenCode</button>}
          <button type="button" className="ghost-btn sm" data-testid="term-shell" onClick={() => start(selected.id, 'shell')}>Shell in worktree</button>
          <span className="spacer" />
          {shown && <button type="button" className="ghost-btn sm" data-testid="term-end" disabled={shown.state === 'opening'} onClick={() => { endEntry(shown); setMode(null) }}>End</button>}
        </div>
      )}
      <div className="term-view" data-testid="term-view" ref={viewRef} style={shown ? undefined : { display: 'none' }} />
      {selected && !shown && (canAttach(selected) ? <p className="muted small">Attach to the session or open a shell in its worktree.</p> : <>
        {selected.agent === 'opencode' && codingLive(selected.status) && <p className="muted small">OpenCode can open this session once the run finishes.</p>}
        <CodingTimeline id={selected.id} />
      </>)}
      {!selected && <p className="muted small">No coding session in this chat yet. Running shell commands show above.</p>}
    </div>
  )
}
