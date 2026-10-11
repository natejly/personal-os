import { useEffect, useRef, useState } from 'react'
import '@xterm/xterm/css/xterm.css'
import type { CodingSession } from '@shared/types'
import { isAttachId } from '@shared/codingAttach'
import { useStore } from '../store'
import { AGENT_LABEL, codingDot, codingStatusLabel } from '../lib/codingSessions'
import { endEntry, ensureEntry, getEntry, refit, startEntry, useTermEntries, type TermMode } from '../lib/termRegistry'

const canAttach = (c: CodingSession): boolean => c.agent === 'claude' && isAttachId(c.external_id)

// The last openTerminal request this pane acted on, so remounting the pane does not replay it.
let handled = 0

/**
 * Terminals for coding sessions: pick a session, then attach to it or open a shell in its worktree. The xterm views live
 * in lib/termRegistry, not in this component, so closing the panel or switching tab only detaches their DOM; the PTYs and
 * their screens are still there when it comes back.
 */
export default function TerminalPane(): JSX.Element {
  const sessions = useStore((s) => s.codingSessions)
  const request = useStore((s) => s.terminalRequest)
  const rows = Object.values(sessions).sort((a, b) => b.created_at - a.created_at).slice(0, 20)
  const [sessionId, setSessionId] = useState<string | null>(request?.sessionId ?? rows[0]?.id ?? null)
  const [mode, setMode] = useState<TermMode | null>(null)
  const entries = useTermEntries()
  const viewRef = useRef<HTMLDivElement>(null)
  const selected = rows.find((c) => c.id === sessionId) ?? null

  const start = (id: string, m: TermMode): void => {
    setSessionId(id); setMode(m)
    void startEntry(ensureEntry(id, m))
  }

  useEffect(() => {
    if (!request || request.n === handled) return
    handled = request.n
    if (request.mode) start(request.sessionId, request.mode)
    else setSessionId(request.sessionId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request])

  // The mode on screen: the one picked, else whichever view of this session already exists.
  const have = (m: TermMode): boolean => !!sessionId && entries.some((e) => e.sessionId === sessionId && e.mode === m)
  const shownMode: TermMode | null = mode && have(mode) ? mode : have('attach') ? 'attach' : have('shell') ? 'shell' : null
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
      <div className="term-chips">
        {rows.map((c) => (
          <button key={c.id} type="button" className={`term-chip${c.id === sessionId ? ' on' : ''}`} data-testid={`term-chip-${c.id}`} title={c.worktree}
            onClick={() => { setSessionId(c.id); setMode(null) }}>
            <span className={`inbox-dot ${codingDot(c.status)}`} role="img" aria-label={codingStatusLabel(c.status)} />
            <span>{AGENT_LABEL[c.agent]}</span>
            <span className="term-chip-branch">{c.branch ?? c.name}</span>
          </button>
        ))}
      </div>
      {selected && (
        <div className="term-actions">
          {canAttach(selected) && <button type="button" className="ghost-btn sm" data-testid="term-attach" onClick={() => start(selected.id, 'attach')}>Attach</button>}
          <button type="button" className="ghost-btn sm" data-testid="term-shell" onClick={() => start(selected.id, 'shell')}>Shell in worktree</button>
          <span className="spacer" />
          {shown && <button type="button" className="ghost-btn sm" data-testid="term-end" disabled={shown.state === 'opening'} onClick={() => { endEntry(shown); setMode(null) }}>End</button>}
        </div>
      )}
      <div className="term-view" data-testid="term-view" ref={viewRef} style={shown ? undefined : { display: 'none' }} />
      {!shown && <p className="muted small">{selected ? 'Attach to the session or open a shell in its worktree.' : 'Pick a coding session.'}</p>}
    </div>
  )
}
