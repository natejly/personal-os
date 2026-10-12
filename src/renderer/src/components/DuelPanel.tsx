import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { useStore } from '../store'
import { ArrowDown, RefreshCw, Maximize2, Shrink } from '../icons'
import MarkdownPreview, { CopyButton } from './MarkdownPreview'
import type { DuelSide } from '@shared/types'

type Side = 'a' | 'b'

/** One blind candidate: header with its tools, the reply as markdown in a body that scrolls on its own. */
function Card({ side, label, hot, wide, canRegen, onRegen, onExpand }: { side: DuelSide; label: string; hot: boolean; wide: boolean; canRegen: boolean; onRegen: () => void; onExpand: () => void }): JSX.Element {
  const body = useRef<HTMLDivElement>(null)
  const stuck = useRef(true) // follow the stream until the reader scrolls up
  const [atBottom, setAtBottom] = useState(true)
  const check = (): void => {
    const el = body.current
    if (!el) return
    const at = el.scrollHeight - el.scrollTop - el.clientHeight < 24
    stuck.current = at
    setAtBottom(at)
  }
  useLayoutEffect(() => {
    const el = body.current
    if (el && stuck.current) el.scrollTop = el.scrollHeight
    check()
  }, [side.text, side.calls.length, side.error, wide])
  return (
    <div className={`duel-card${hot ? ' hot' : ''}${wide ? ' wide' : ''}`}>
      <div className="duel-head">
        <b>{label}</b>
        {!side.done && <small>streaming…</small>}
        <span className="duel-tools">
          <button className="icon-btn ghost" title="Regenerate this side" aria-label={`Regenerate ${label}`} disabled={!canRegen} onClick={onRegen}><RefreshCw size={13} /></button>
          <CopyButton text={side.text} />
          <button className="icon-btn ghost" title={wide ? 'Collapse' : 'Expand'} aria-label={wide ? `Collapse ${label}` : `Expand ${label}`} aria-pressed={wide} onClick={onExpand}>{wide ? <Shrink size={13} /> : <Maximize2 size={13} />}</button>
        </span>
      </div>
      <div className="duel-body-wrap">
        <div className="duel-body" ref={body} onScroll={check}>
          {side.text ? <div className={side.done ? 'markdown' : 'markdown streaming'}><MarkdownPreview source={side.text} streaming={!side.done} /></div>
            : side.done && !side.error ? <small className="duel-empty">(no text)</small> : null}
          {side.error && <div className="notice error" role="alert">{side.error}</div>}
          {side.calls.length > 0 && (
            <div className="duel-calls">
              <small>Wants to run (not run during the duel):</small>
              {side.calls.map((c, i) => <code key={i}>{c.name}({c.args})</code>)}
            </div>
          )}
        </div>
        {!atBottom && <button className="duel-jump" title="Scroll to bottom" aria-label="Scroll to bottom" onClick={() => { const el = body.current; if (el) { stuck.current = true; el.scrollTop = el.scrollHeight } }}><ArrowDown size={13} /></button>}
      </div>
    </div>
  )
}

/** The two replies of a developer-mode duel, side by side and blind (no model names), until one is picked. */
export default function DuelPanel({ conversationId }: { conversationId: string | undefined }): JSX.Element | null {
  const duel = useStore((s) => (conversationId ? s.sessions[conversationId]?.duel : null))
  const toast = useStore((s) => s.toast)
  const [hot, setHot] = useState<Side | null>(null)
  const [wide, setWide] = useState<Side | null>(null)
  const root = useRef<HTMLDivElement>(null)
  // Wider than the message column: as wide as the scrolling chat pane (minus gutters), whatever the sidebar is doing.
  useEffect(() => {
    const pane = root.current?.closest('.messages')
    if (!pane) return
    const set = (): void => root.current?.style.setProperty('--duel-w', `${Math.min(1280, pane.clientWidth - 48)}px`)
    set()
    const ro = new ResizeObserver(set)
    ro.observe(pane)
    return () => ro.disconnect()
  }, [!!duel])
  useEffect(() => { if (!duel) { setHot(null); setWide(null) } }, [duel])
  if (!conversationId || !duel) return null
  const fail = (e: Error): void => toast(e.message, 'error')
  const pick = (w: Side): void => { void api.abPick(conversationId, duel.messageId, w).catch(fail) }
  const regen = (s: Side): void => { void api.abRegenerate(conversationId, duel.messageId, s).catch(fail) }
  const lit = (s: Side) => ({ onMouseEnter: () => setHot(s), onMouseLeave: () => setHot(null), onFocus: () => setHot(s), onBlur: () => setHot(null) })
  return (
    <div className="duel" role="group" aria-label="Model duel" ref={root}>
      <div className="duel-cols">
        {(['a', 'b'] as const).map((s) => (
          <Card key={s} side={duel[s]} label={`Assistant ${s.toUpperCase()}`} hot={hot === s} wide={wide === s} canRegen={duel.ready}
            onRegen={() => regen(s)} onExpand={() => setWide(wide === s ? null : s)} />
        ))}
      </div>
      <div className="duel-pick">
        <button className="ghost-btn" disabled={!duel.ready || !!duel.a.error} onClick={() => pick('a')} {...lit('a')}>← A is better</button>
        <button className="ghost-btn" disabled={!duel.ready || !!duel.b.error} onClick={() => pick('b')} {...lit('b')}>B is better →</button>
      </div>
    </div>
  )
}
