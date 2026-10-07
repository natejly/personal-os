import { useEffect, useRef, useState } from 'react'
import { Bot } from 'lucide-react'
import type { DeskAutonomy } from '@shared/types'
import { useStore } from '../store'
import { AUTONOMY } from '../lib/deskStatus'
import { useChatDesk } from './DeskStrip'
import { startAutonomy } from '../lib/autonomyDefault'

/**
 * "Mode": the chat hands its task to a desk that keeps working in this same conversation, in
 * turns, until it is done or needs you. Off stops it and the chat answers as a plain chat again; the
 * desk's workspace is kept, and turning it back on picks the same one up. Autonomy changes take effect on the
 * next turn (the backend reads it off the desk row).
 *
 * On the main new-chat composer (`draft`) there is no chat yet, so the toggle is armed from Settings → Advanced →
 * Desks ("Start new chats working autonomously") and the menu picks the level, or turns it off, for this draft only.
 */
export default function AutonomyToggle({ conversationId, draft = false }: { conversationId?: string; draft?: boolean }): JSX.Element {
  const convId = useStore((s) => conversationId ?? s.focusedConversationId)
  const deskId = useStore((s) => (convId ? s.sessions[convId]?.conversation.settings.deskId : undefined) || undefined)
  const desk = useChatDesk(deskId)
  const busy = useStore((s) => s.deskBusy)
  // The global desk permissions every autonomous chat inherits (Settings → Permissions → Desks).
  const shellAuto = useStore((s) => s.settings.deskShellAuto !== false)
  const doneGate = useStore((s) => s.settings.deskDoneGate !== false)
  const openSettings = useStore((s) => s.openSettings)
  const { workAutonomously, stopWorkingAutonomously, patchDesk, setDraftAutonomy } = useStore()
  const draftLevel = useStore((s) => (draft && !convId ? startAutonomy({ autonomousByDefault: s.settings.autonomousByDefault, draft: s.draftAutonomy, mainComposer: true, agent: s.draftChatSettings.agent }) : null))
  const [open, setOpen] = useState(false)
  const [autonomy, setAutonomy] = useState<DeskAutonomy>('ask')
  const box = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent): void => { if (!box.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent): void => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [open])

  const armed = draftLevel !== null
  const on = !!deskId || armed
  const current = armed ? draftLevel : deskId ? desk?.autonomy ?? 'plan' : autonomy
  const label = AUTONOMY.find((a) => a.value === current)?.label
  const start = async (): Promise<void> => {
    if (!convId) return
    setOpen(false)  // now, not after the desk starts: a click in between must find the menu closed
    await workAutonomously(convId, autonomy)
  }
  return (
    <span className="autonomy-ctl" ref={box}>
      <button className={`composer-ctl autonomy ${on ? 'on' : ''}`} aria-pressed={on} aria-expanded={open} disabled={!convId && !draft}
        aria-label="Mode: how the assistant works in this chat"
        title={convId || draft ? 'Mode: how the assistant works in this chat' : 'Send a message first: it works on what this chat is about'}
        onClick={() => setOpen((o) => !o)}>
        <Bot size={13} /> {on ? `Mode: ${label}` : 'Mode'}
      </button>
      {open && (
        <div className="autonomy-menu" role="dialog" aria-label="Mode">
          <div className="desk-autonomy">
            {AUTONOMY.map((a) => (
              <label key={a.value} className={`desk-autonomy-opt ${current === a.value ? 'on' : ''}`}>
                <input type="radio" name={`autonomy-${convId ?? 'draft'}`} checked={current === a.value}
                  onChange={() => (deskId ? void patchDesk(deskId, { autonomy: a.value }) : draft && !convId ? setDraftAutonomy(a.value) : setAutonomy(a.value))} />
                <b>{a.label}</b>
                <small>{a.hint}</small>
              </label>
            ))}
          </div>
          <p className="muted small">
            From Settings: sandboxed commands in its folder {shellAuto ? 'run without asking' : 'ask first'}; finishing checks {doneGate ? 'on' : 'off'}.{' '}
            <button type="button" className="link small" onClick={() => { setOpen(false); openSettings('permissions') }}>Change</button>
          </p>
          <div className="approval-actions">
            {armed
              ? <button className="ghost-btn danger" onClick={() => { setOpen(false); setDraftAutonomy('off') }}>Turn off</button>
              : deskId
                ? <button className="ghost-btn danger" onClick={() => { setOpen(false); void stopWorkingAutonomously(convId!) }}>Turn off</button>
                : !draft && <button className="primary-btn" disabled={busy} onClick={() => void start()}>Start working</button>}
          </div>
        </div>
      )}
    </span>
  )
}
