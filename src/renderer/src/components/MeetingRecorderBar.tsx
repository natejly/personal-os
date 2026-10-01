import { useEffect } from 'react'
import { AlertTriangle, Mic, Pause, Play, Speaker, Square } from 'lucide-react'
import { useStore } from '../store'
import { formatOffset, recorderState, type RecorderState } from '../lib/transcript'

/**
 * The live bar above the notepad: how long it has been running, which channels are actually
 * capturing, and how far behind the transcript is.
 *
 * The latency line is the point of this component. The segment muxer closes a clip every
 * `segmentSeconds`, and that clip then queues for transcription, so words cannot appear sooner than
 * that no matter what the UI implies - a spinner here would be a lie with an animation. The bar
 * states the delay and the queue depth instead.
 */

/** What the bar calls each recorder state. `stalled` is its own word on purpose: it is not a pause,
 *  and Resume cannot fix it. */
const STATE_LABEL: Record<RecorderState, string> = {
  recording: 'Recording', paused: 'Paused', stalled: 'Capture stopped'
}

const CHANNEL_LABEL: Record<string, { label: string; icon: JSX.Element }> = {
  mic: { label: 'microphone', icon: <Mic size={12} /> },
  output: { label: 'system audio', icon: <Speaker size={12} /> },
  import: { label: 'imported audio', icon: <Speaker size={12} /> }
}

export default function MeetingRecorderBar(): JSX.Element | null {
  const meetingStatus = useStore((s) => s.meetingStatus)
  const meetingBusy = useStore((s) => s.meetingBusy)
  const pollMeetingLive = useStore((s) => s.pollMeetingLive)
  const refreshMeetings = useStore((s) => s.refreshMeetings)
  const stopRecording = useStore((s) => s.stopRecording)
  const pauseMeeting = useStore((s) => s.pauseMeeting)
  const resumeMeeting = useStore((s) => s.resumeMeeting)

  const active = meetingStatus?.active ?? null
  // Keyed on the id rather than the object: `active` is replaced by every poll, and depending on it
  // would tear the intervals down and rebuild them twice a second.
  const liveId = active?.meeting_id ?? ''

  // There is no app-wide SSE topic in this checkout, so the live numbers are polled on the pair of
  // intervals ActivityView settled on: the segment tail every 2s, the rail every 5s.
  useEffect(() => {
    if (!liveId) return
    const tail = setInterval(() => void pollMeetingLive(), 2000)
    // No argument: `refreshMeetings` re-issues the rail's own search query, so this tick cannot
    // replace a filtered list with the unfiltered one under a search box the user is still typing in.
    const rail = setInterval(() => void refreshMeetings(), 5000)
    return () => { clearInterval(tail); clearInterval(rail) }
  }, [liveId, pollMeetingLive, refreshMeetings])

  if (!active) return null

  // Read off the flag the recorder emits, never off the channels: pause deliberately leaves ffmpeg
  // running and only discards the clips, so every channel stays `alive` through a pause. The dots
  // below still show per-channel liveness, which is a different question.
  const state = recorderState(active)
  const behind = meetingStatus?.config.segmentSeconds ?? 0

  return (
    <div className="mtg-bar">
      <div className={`act-state ${state === 'recording' ? 'live' : 'paused'}`}>
        <span className="act-dot" />
        {STATE_LABEL[state]}
      </div>
      <span className="mtg-clock">{formatOffset(active.elapsed_ms / 1000)}</span>

      <div className="mtg-channels">
        {active.channels.map((c) => {
          const info = CHANNEL_LABEL[c.channel] ?? { label: c.channel, icon: <Mic size={12} /> }
          return (
            <span key={c.channel} className={`mtg-channel ${c.error ? 'bad' : ''}`} title={c.error || undefined}>
              <span className={`mtg-dot ${c.error ? 'failed' : c.alive ? 'live' : ''}`} />
              {info.icon}
              {info.label}
              {c.error && <em className="mtg-channel-error">: {c.error}</em>}
            </span>
          )
        })}
        {active.channels.length === 0 && (
          <span className="mtg-channel bad"><AlertTriangle size={12} /> nothing is capturing</span>
        )}
      </div>

      {/* The clock and these counts advance with the 2s poll rather than a local timer, so they can
          never drift away from what the recorder actually holds. */}
      <span className="mtg-lag">
        {state === 'paused'
          ? 'paused · audio is being discarded'
          : <>
              transcript ~{behind}s behind
              {active.queued > 0 ? ` · ${active.queued} queued` : ''}
              {active.segments_done > 0 ? ` · ${active.segments_done} transcribed` : ''}
            </>}
      </span>

      <div className="mtg-bar-actions">
        {/* No Resume when the captures are dead: resuming only clears the flag, and cannot respawn
            an ffmpeg that exited, so the button would do nothing. Stop is the way out of that. */}
        {state === 'recording' && (
          <button className="ghost-btn" disabled={meetingBusy} onClick={() => void pauseMeeting()}><Pause size={13} /> Pause</button>
        )}
        {state === 'paused' && (
          <button className="ghost-btn" disabled={meetingBusy} onClick={() => void resumeMeeting()}><Play size={13} /> Resume</button>
        )}
        <button className="ghost-btn danger" disabled={meetingBusy} onClick={() => void stopRecording()}>
          <Square size={13} /> Stop
        </button>
      </div>
    </div>
  )
}
