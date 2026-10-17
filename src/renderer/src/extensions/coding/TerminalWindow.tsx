import { useEffect, useRef } from 'react'
import '@xterm/xterm/css/xterm.css'
import { useCoding } from './store'
import { AGENT_LABEL } from './lib/codingSessions'
import { endEntry, getEntry, refit, useTermEntries, type TermMode } from './termRegistry'

/** The attached terminal: a large floating window over the chat. Closing only detaches the xterm DOM; the PTY keeps running. */
export default function TerminalWindow({ sessionId, mode, onClose }: { sessionId: string; mode: TermMode; onClose: () => void }): JSX.Element {
  const c = useCoding((s) => s.sessions[sessionId])
  useTermEntries() // re-render when the entry appears or is ended
  const entry = getEntry(sessionId, mode)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = box.current
    if (!el || !entry) return
    el.appendChild(entry.host)
    let timer: ReturnType<typeof setTimeout> | undefined
    const ro = new ResizeObserver(() => { clearTimeout(timer); timer = setTimeout(() => refit(entry), 100) })
    ro.observe(el)
    const raf = requestAnimationFrame(() => { refit(entry); entry.term.focus() })
    return () => { ro.disconnect(); clearTimeout(timer); cancelAnimationFrame(raf); if (entry.host.parentElement === el) entry.host.remove() }
  }, [entry])
  useEffect(() => {
    const k = (e: KeyboardEvent): void => { if (e.metaKey && e.key === 'w') { e.preventDefault(); onClose() } } // Esc belongs to the terminal
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])
  const name = mode === 'agent' ? 'Agent terminal' : c ? `${AGENT_LABEL[c.agent]} · ${c.branch ?? c.name}` : sessionId
  return (
    <div className="term-window" data-testid="term-window" role="dialog" aria-label={`Terminal: ${name}`}>
      <div className="term-window-head">
        <b>{name}</b><span className="muted">{mode === 'shell' ? 'shell in worktree' : mode === 'agent' ? 'sandboxed, opened by an agent' : mode}</span>
        <span className="spacer" />
        {entry && <button type="button" className="ghost-btn sm" data-testid="term-window-end" disabled={entry.state === 'opening'} onClick={() => { endEntry(entry); onClose() }}>End</button>}
        <button type="button" className="ghost-btn sm" data-testid="term-detach" title="Detach (it keeps running)" onClick={onClose}>Detach</button>
      </div>
      <div className="term-window-view" data-testid="term-view" ref={box} />
    </div>
  )
}
