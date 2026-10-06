import { AlertTriangle, Copy, Loader2, Pause, Play, Square } from 'lucide-react'
import { useStore } from '../../store'
import { formatOffset, recorderState } from '../../lib/transcript'
import { useDocRec } from './store'
import { useDocRecSync, useElapsed } from './hooks'
import { behindLabel, silentLabel } from './behind'
import { HEADS_UP_MESSAGE, modeLabel } from './format'
import '../../styles/docrec.css'

/**
 * The slim bar shown while THIS doc records: state, elapsed time, how far behind the transcript
 * is, a warning when capture is not working, and Pause/Stop. Renders nothing otherwise, except for
 * a one-line "finishing" strip after Stop while the transcript and summary are still arriving.
 */
export default function DocRecorderBar({ docId }: { docId: string }): JSX.Element | null {
  useDocRecSync(docId)
  const status = useStore((s) => s.meetingStatus)
  const busy = useDocRec((s) => s.busy)
  const settling = useDocRec((s) => s.settling)
  const raw = status?.active ?? null
  const active = raw && raw.doc_id === docId ? raw : null
  const elapsed = useElapsed(active)

  if (!active) {
    if (settling?.docId !== docId) return null
    return (
      <div className="dr-bar settling" role="status">
        <Loader2 size={13} className="spin" />
        <span>Finishing the transcript{settling.wantSummary ? ' and writing the summary' : ''}</span>
      </div>
    )
  }

  // Read off the flag the recorder emits: pause leaves capture running and only discards audio, so
  // channels stay alive through a pause. A session whose captures all died is neither.
  const state = recorderState(active)
  const dictating = active.doc_mode === 'dictate'
  // The first channel that is erroring, or dead while the recorder claims to be recording.
  const broken = active.channels.find((c) => c.error) ?? (state === 'stalled' ? active.channels[0] : undefined)
  const warning = active.error
    || (broken ? `${broken.channel === 'mic' ? 'Microphone' : broken.channel === 'output' ? 'System audio' : broken.channel} is not capturing${broken.error ? `: ${broken.error}` : ''}` : '')
    || (active.channels.length === 0 ? 'Nothing is capturing' : '')
  const mic = active.channels.find((c) => c.channel === 'mic')
  const silent = state === 'recording' ? silentLabel(mic?.silent_for_s) : ''
  const lag = behindLabel(active.segment_seconds, active.queued, state === 'paused')

  return (
    <div className={`dr-bar ${state}`} role="status">
      <span className="dr-dot" aria-hidden />
      <span className="dr-bar-mode">{state === 'paused' ? 'Paused' : state === 'stalled' ? 'Capture stopped' : dictating ? 'Dictating' : modeLabel(active.doc_mode)}</span>
      <span className="dr-clock">{formatOffset(elapsed)}</span>
      {lag && <span className="dr-lag">{lag}</span>}
      {silent && <span className="dr-warn" title={silent}><AlertTriangle size={12} /> {silent}</span>}
      {active.auto_paused && state === 'paused' && <span className="dr-warn">Still recording?</span>}
      {warning && !(active.auto_paused && warning.startsWith('Paused after')) && <span className="dr-warn" title={warning}><AlertTriangle size={12} /> {warning}</span>}
      <span className="dr-bar-actions">
        <button className="ghost-btn dr-small" title="Copy a message to paste into the call" onClick={() => void navigator.clipboard.writeText(HEADS_UP_MESSAGE).catch(() => undefined)}><Copy size={12} /> Copy heads-up</button>
        {state === 'recording' && (
          <button className="ghost-btn dr-small" disabled={busy} onClick={() => void useDocRec.getState().pause()}><Pause size={12} /> Pause</button>
        )}
        {state === 'paused' && (
          <button className="ghost-btn dr-small" disabled={busy} onClick={() => void useDocRec.getState().resume()}><Play size={12} /> Resume</button>
        )}
        <button className="ghost-btn dr-small danger" disabled={busy} onClick={() => void useDocRec.getState().stop()}><Square size={12} /> Stop</button>
      </span>
    </div>
  )
}
