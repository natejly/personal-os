import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Check, Send, Undo2, X } from 'lucide-react'
import { api, verificationMessage } from '../lib/api'
import { useStore } from '../store'
import { pimConnected } from '../lib/pim'
import type { PendingSend } from '@shared/types'

/** Sits next to the toasts: every email still inside its undo window, counting down.
 *
 *  It polls rather than listening, because a send can be queued by the assistant mid-reply, by the
 *  compose window, or by another window of the app, and the countdown has to be right in all three.
 *  Polling only runs while something is in flight (and never on the backend's own clock: the row
 *  carries seconds_left, which this ticks down locally between refreshes). */
export default function PendingSends(): JSX.Element | null {
  const [sends, setSends] = useState<PendingSend[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const toast = useStore((s) => s.toast)
  const connected = useStore(pimConnected)

  // `issued` numbers each request and `applied` is the newest one shown, so a slow older response
  // cannot overwrite a newer one; `inflight` lets the 1s tick skip rather than stack requests.
  const issued = useRef(0)
  const applied = useRef(0)
  const inflight = useRef(0)
  const refresh = useCallback(async (opts: { tick?: boolean } = {}): Promise<void> => {
    if (opts.tick && inflight.current > 0) return
    const mine = ++issued.current
    inflight.current++
    try {
      const r = await api.outbox.list()
      if (mine > applied.current) {
        applied.current = mine
        setSends(r.sends)
      }
    } catch {
      /* a missing outbox is not worth a toast */
    } finally {
      inflight.current--
    }
  }, [])

  useEffect(() => {
    if (!connected) return
    void refresh()
    // Anything that queues mail (Mail's Send, an accepted proposal, the assistant's gmail_send) says so, so the undo
    // countdown shows up at once instead of on the next launch.
    const on = (): void => void refresh()
    window.addEventListener('grain-outbox-changed', on)
    return (): void => window.removeEventListener('grain-outbox-changed', on)
  }, [connected, refresh])

  // One shared second-tick: counts down locally, and re-reads the server as each hold runs out so
  // the row flips to its real outcome (sent + verified, or a loud warning).
  const live = sends.some((s) => s.status === 'holding' || s.status === 'sending')
  useEffect(() => {
    if (!live) return
    const t = setInterval(() => {
      setSends((rows) => rows.map((r) => (r.status === 'holding' ? { ...r, seconds_left: Math.max(0, r.seconds_left - 1) } : r)))
      void refresh({ tick: true })
    }, 1000)
    return (): void => clearInterval(t)
  }, [live, refresh])

  const act = async (id: string, what: 'cancel' | 'sendNow'): Promise<void> => {
    setBusy(id)
    try {
      const r = what === 'cancel' ? await api.outbox.cancel(id) : await api.outbox.sendNow(id)
      toast(what === 'cancel' ? 'Send cancelled — nothing went out.' : r.verified === false ? 'Sent, but unconfirmed.' : 'Sent.')
      await refresh()
    } catch (e) {
      toast((e as Error).message, 'error')
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  const shown = sends.filter((s) => s.status !== 'cancelled' && !(s.status === 'sent' && s.verified !== false))
  if (!shown.length) return null
  return (
    <div className="pending-sends">
      {shown.map((s) => (
        <div key={s.id} className={`pending-send ${s.status} ${s.verified === false ? 'unproven' : ''}`}>
          <span className="pending-icon">
            {s.status === 'holding' || s.status === 'sending' ? <Send size={13} /> : s.verified === false || s.error ? <AlertTriangle size={13} /> : <Check size={13} />}
          </span>
          <div className="pending-text">
            <div className="pending-head">
              {s.status === 'holding' ? (
                <>
                  Sending in <b>{s.seconds_left}s</b>
                </>
              ) : s.status === 'sending' ? (
                'Sending…'
              ) : s.status === 'expired' ? (
                'Never sent'
              ) : s.status === 'failed' ? (
                'Send failed'
              ) : (
                'Sent, but unconfirmed'
              )}
              {s.origin === 'assistant' && s.status === 'holding' ? ' · from the assistant' : ''}
            </div>
            <div className="pending-sub">
              {s.subject || '(no subject)'} → {s.to}
            </div>
            {(s.error || s.verification) && s.status !== 'holding' && (
              <div className="pending-why">{s.error ?? (s.verification ? verificationMessage(s.verification) : '')}</div>
            )}
          </div>
          <div className="pending-actions">
            {s.status === 'holding' && (
              <>
                <button className="ghost-btn" disabled={busy === s.id} onClick={() => void act(s.id, 'cancel')}>
                  <Undo2 size={13} /> Undo
                </button>
                <button className="ghost-btn" disabled={busy === s.id} onClick={() => void act(s.id, 'sendNow')}>
                  Send now
                </button>
              </>
            )}
            {(s.status === 'expired' || s.status === 'failed') && (
              <>
                {s.status === 'expired' && (
                  <button className="ghost-btn" disabled={busy === s.id} onClick={() => void act(s.id, 'sendNow')}>
                    Send now
                  </button>
                )}
                <button className="ghost-btn" disabled={busy === s.id} onClick={() => void act(s.id, 'cancel')} title="Discard">
                  <X size={13} />
                </button>
              </>
            )}
            {s.status === 'sent' && (
              <button className="ghost-btn" onClick={() => setSends((rows) => rows.filter((r) => r.id !== s.id))} title="Dismiss">
                <X size={13} />
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
