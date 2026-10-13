import { useEffect, useRef, useState } from 'react'
import '@xterm/xterm/css/xterm.css'
import { useStore } from '../store'
import { AGENT_LABEL, canAttach, codingDot, codingStatusLabel, defaultTermSession, termSessions } from '../lib/codingSessions'
import { ensureEntry, getEntry, refit, startEntry, useTermEntries } from '../lib/termRegistry'
import { CodingTimeline } from './CodingTimeline'

// The last openTerminal request this pane acted on, so remounting the pane does not replay it.
let handled = 0
// The session on screen when the pane last unmounted, so coming back from another tab shows what you left.
let last: string | null = null

/**
 * Terminals for coding sessions: picking a session shows it at once. One that can attach (claude with an attach id) opens its
 * attached terminal; any other (OpenCode, pi) shows its live activity timeline, read-only. The chips list the chat's own
 * sessions and only appear when there is more than one. The xterm views live in lib/termRegistry, not in this component, so
 * closing the panel or switching tab only detaches their DOM; the PTYs and their screens are still there when it comes back.
 */
export default function TerminalPane({ conversationId }: { conversationId?: string }): JSX.Element {
  const sessions = useStore((s) => s.codingSessions)
  const request = useStore((s) => s.terminalRequest)
  const rows = termSessions(Object.values(sessions), conversationId, request?.sessionId)
  const kept = last && rows.some((c) => c.id === last) ? last : null
  const [sessionId, setSessionId] = useState<string | null>(request?.sessionId ?? kept ?? defaultTermSession(rows)?.id ?? null)
  useEffect(() => { if (sessionId) last = sessionId }, [sessionId])
  useTermEntries()
  const viewRef = useRef<HTMLDivElement>(null)
  const selected = rows.find((c) => c.id === sessionId) ?? null

  // Picking a session shows it at once: attached when it can be, else its timeline (below).
  const pick = (id: string): void => {
    setSessionId(id)
    const c = sessions[id]
    if (c && canAttach(c)) void startEntry(ensureEntry(id))
  }

  // No fresh request and nothing left open: show the default session once, as soon as there is one (sessions can arrive after mount).
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
    pick(request.sessionId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request])

  const attachable = !!selected && canAttach(selected)
  // A session gets its attach id a moment after it starts: attach as soon as it can.
  useEffect(() => { if (attachable && sessionId && !getEntry(sessionId)) void startEntry(ensureEntry(sessionId)) }, [attachable, sessionId])
  const shown = attachable && sessionId ? getEntry(sessionId) ?? null : null

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
      {rows.length > 1 && (
        <div className="term-chips">
          {rows.map((c) => (
            <button key={c.id} type="button" className={`term-chip${c.id === sessionId ? ' on' : ''}`} data-testid={`term-chip-${c.id}`} title={c.worktree}
              onClick={() => pick(c.id)}>
              <span className={`inbox-dot ${codingDot(c.status)}`} role="img" aria-label={codingStatusLabel(c.status)} />
              <span>{AGENT_LABEL[c.agent]}</span>
              <span className="term-chip-branch">{c.branch ?? c.name}</span>
            </button>
          ))}
        </div>
      )}
      <div className="term-view" data-testid="term-view" ref={viewRef} style={shown ? undefined : { display: 'none' }} />
      {selected && !shown && <CodingTimeline id={selected.id} />}
      {!selected && <p className="muted small">Pick a coding session.</p>}
    </div>
  )
}
