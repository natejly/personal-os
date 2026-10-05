import { useEffect, useRef, useState } from 'react'
import { Bot } from 'lucide-react'
import type { DeskAutonomy } from '@shared/types'
import { useStore } from '../store'
import { AUTONOMY } from '../lib/deskStatus'
import { useChatDesk } from './DeskStrip'

/**
 * "Work autonomously": the chat hands its task to a desk that keeps working in this same conversation, in
 * bounded turns, until it is done or needs you. Off stops it and the chat answers as a plain chat again; the
 * desk's workspace is kept, and turning it back on picks the same one up. Autonomy changes take effect on the
 * next turn (the backend reads it off the desk row); limits can only be tighter than the Settings caps.
 */
export default function AutonomyToggle({ conversationId }: { conversationId?: string }): JSX.Element {
  const convId = useStore((s) => conversationId ?? s.focusedConversationId)
  const deskId = useStore((s) => (convId ? s.sessions[convId]?.conversation.settings.deskId : undefined) || undefined)
  const desk = useChatDesk(deskId)
  const busy = useStore((s) => s.deskBusy)
  const maxTurnsDefault = useStore((s) => s.settings.deskMaxTurns ?? 12)
  const { workAutonomously, stopWorkingAutonomously, patchDesk } = useStore()
  const [open, setOpen] = useState(false)
  const [autonomy, setAutonomy] = useState<DeskAutonomy>('plan')
  const [turns, setTurns] = useState('')
  const box = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent): void => { if (!box.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent): void => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [open])

  const on = !!deskId
  const current = on ? desk?.autonomy ?? 'plan' : autonomy
  const label = AUTONOMY.find((a) => a.value === current)?.label
  const start = async (): Promise<void> => {
    if (!convId) return
    setOpen(false)  // now, not after the desk starts: a click in between must find the menu closed
    await workAutonomously(convId, autonomy, Number(turns) > 0 ? { maxTurns: Number(turns) } : undefined)
  }
  return (
    <span className="autonomy-ctl" ref={box}>
      <button className={`composer-ctl autonomy ${on ? 'on' : ''}`} aria-pressed={on} aria-expanded={open} disabled={!convId}
        title={convId ? 'Let it keep working on this on its own, in its own folder, until it is done or needs you' : 'Send a message first: it works on what this chat is about'}
        onClick={() => setOpen((o) => !o)}>
        <Bot size={13} /> {on ? `Autonomous: ${label}` : 'Work autonomously'}
      </button>
      {open && (
        <div className="autonomy-menu" role="dialog" aria-label="Work autonomously">
          <div className="desk-autonomy">
            {AUTONOMY.map((a) => (
              <label key={a.value} className={`desk-autonomy-opt ${current === a.value ? 'on' : ''}`}>
                <input type="radio" name={`autonomy-${convId}`} checked={current === a.value}
                  onChange={() => (on && deskId ? void patchDesk(deskId, { autonomy: a.value }) : setAutonomy(a.value))} />
                <b>{a.label}</b>
                <small>{a.hint}</small>
              </label>
            ))}
          </div>
          {!on && (
            <label className="desk-limits">Turns <input type="number" min={1} step={1} placeholder={String(maxTurnsDefault)} value={turns} onChange={(e) => setTurns(e.target.value)} /></label>
          )}
          <div className="approval-actions">
            {on
              ? <button className="ghost-btn danger" onClick={() => { setOpen(false); void stopWorkingAutonomously(convId!) }}>Turn off</button>
              : <button className="primary-btn" disabled={busy} onClick={() => void start()}>Start working</button>}
          </div>
        </div>
      )}
    </span>
  )
}
