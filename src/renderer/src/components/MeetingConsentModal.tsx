import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, Copy, Mic } from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import { HEADS_UP_MESSAGE } from '../features/docrec/format'
import { useModal } from '../lib/useModal'
import { CapActions } from './MeetingSettings'

/**
 * The one-time consent gate, shown before the first recording this install ever makes.
 *
 * It exists because recording a call captures people who never opened this app. So it names the
 * exact directory the audio is written to and the exact base URL each clip is uploaded to - not
 * "securely stored", not "your data stays private" - and it will not enable anything until the
 * person has ticked a box saying they will tell the others on the call. The acknowledgement is
 * stored as `consentedAt` so this is asked once and then never again.
 */

export default function MeetingConsentModal(): JSX.Element {
  const settings = useStore((s) => s.settings)
  const meetingPreflight = useStore((s) => s.meetingPreflight)
  const meetingBusy = useStore((s) => s.meetingBusy)
  const loadMeetingPreflight = useStore((s) => s.loadMeetingPreflight)
  const setMeetingConsentOpen = useStore((s) => s.setMeetingConsentOpen)
  const acceptMeetingConsent = useStore((s) => s.acceptMeetingConsent)
  const [ack, setAck] = useState(false)
  const [dataDir, setDataDir] = useState('')
  const { titleId, backdrop, modal } = useModal(() => setMeetingConsentOpen(false))

  // The path is the backend's, so it is read from the backend rather than guessed from the platform.
  useEffect(() => {
    void api.health().then((h) => setDataDir(h.data_dir)).catch(() => undefined)
    void loadMeetingPreflight()
  }, [loadMeetingPreflight])

  const bad = (meetingPreflight?.capabilities ?? []).filter((c) => !c.ok)

  // Portaled: it opens from inside the Docs page, whose size container would otherwise box the backdrop in.
  return createPortal(
    <div className="modal-backdrop" {...backdrop}>
      <div className="modal" {...modal}>
        <header>
          <h2 id={titleId}><Mic size={16} /> Before the first recording</h2>
        </header>

        <section className="mtg-consent">
          <p>
            Turning this on records your microphone while a meeting is running. If system audio
            capture is available it also records everything your speakers play, which on a call
            means every other person in it. They will not be told by this app, and depending on
            where you are, recording them without saying so may be illegal.
          </p>
          <ul>
            <li>
              Each clip is written as a wav under <code>{dataDir ? `${dataDir}/recordings/` : '…/recordings/'}</code>,
              one folder per meeting. Nothing is written to a temp directory that gets swept.
            </li>
            <li>
              Each clip is transcribed on this Mac when Speech or whisper.cpp is selected. Otherwise
              it is uploaded to <code>{settings.baseUrl || '(no base URL configured)'}</code>, and
              goes nowhere else. Whatever that proxy does with it is between you and whoever runs it.
            </li>
            <li>
              The wavs are deleted once a clip has been transcribed, unless you switch on
              <b> Keep audio</b>. The notes and the transcript stay on this machine until you delete
              the meeting.
            </li>
            <li>
              Nothing said on a call is turned into a durable memory, and no tool can start, stop or
              pause a recording. That stays a button you press.
            </li>
          </ul>

          {bad.length > 0 && (
            <>
              <p className="muted small">
                This machine cannot do all of it yet. Fixing it now beats finding out at the start of
                the call:
              </p>
              <ul className="act-caps">
                {bad.map((c) => (
                  <li key={c.id} className="bad">
                    <AlertTriangle size={13} />
                    <div>
                      <b>{c.label}</b>
                      <p>{c.detail}</p>
                      {c.fix && <p className="act-fix">{c.fix}</p>}
                      <CapActions cap={c} />
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}

          <button className="ghost-btn dr-small" onClick={() => void navigator.clipboard.writeText(HEADS_UP_MESSAGE).catch(() => undefined)}><Copy size={12} /> Copy a heads-up message for the call</button>

          <label className="mtg-consent-ack">
            <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
            <span>I will tell the other people on the call that I am recording.</span>
          </label>
        </section>

        <footer>
          <button className="ghost-btn" onClick={() => setMeetingConsentOpen(false)}>Not now</button>
          <button className="primary-btn" disabled={!ack || meetingBusy} onClick={() => void acceptMeetingConsent()}>
            I understand, start recording
          </button>
        </footer>
      </div>
    </div>,
    document.body
  )
}
