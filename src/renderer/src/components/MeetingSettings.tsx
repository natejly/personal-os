import { useEffect, useState } from 'react'
import {
  AlertTriangle, Check, Copy, FileText, Mic, RefreshCw, Shield, Sparkles, Trash2, Zap
} from 'lucide-react'
import { useStore } from '../store'
import { api } from '../lib/api'
import type { MeetingCapability, MeetingConfig, MeetingTemplate } from '@shared/types'
import { AudioDevicePicker } from './ActivityView'

/**
 * Everything the recorder needs set up, and every switch that turns it off. Doubles as the
 * Meetings view's empty state, so the first thing a new install sees is the checklist of what this
 * machine can actually do rather than an invitation to record into a 404.
 *
 * Start is blocked, not warned about, when a blocker is standing: the activity monitor's
 * transcription row reports ok as long as the model name is a non-empty string, which is how you
 * end up with an hour of wavs and no words. The self-test below does a real round trip instead.
 */

const TEMPLATE_LABEL: Record<MeetingTemplate, string> = {
  general: 'General',
  standup: 'Standup',
  one_on_one: 'One on one',
  user_interview: 'User interview',
  sales_call: 'Sales call',
  lecture: 'Lecture'
}

const MIB = 1024 * 1024

/** Mirrors ActivityView's NumberField, which is local to that file; only the device picker is
 *  exported from it, so this is a deliberate copy rather than a second export. */
function NumberField({ label, hint, value, min, max, step = 1, suffix, onCommit }: {
  label: string; hint?: string; value: number; min: number; max: number; step?: number; suffix: string
  onCommit: (v: number) => void
}): JSX.Element {
  const [draft, setDraft] = useState(String(value))
  useEffect(() => setDraft(String(value)), [value])
  const commit = (): void => {
    const n = Number(draft)
    if (Number.isFinite(n) && n >= min && n <= max) onCommit(n)
    else setDraft(String(value))
  }
  return (
    <label className="act-field">
      <span><b>{label}</b>{hint && <small>{hint}</small>}</span>
      <span className="act-num">
        <input type="number" min={min} max={max} step={step} value={draft}
          onChange={(e) => setDraft(e.target.value)} onBlur={commit}
          onKeyDown={(e) => { if (e.key === 'Enter') commit() }} />
        <em>{suffix}</em>
      </span>
    </label>
  )
}

/** One checklist row, with the fix copyable: the fix is usually a shell command. */
function CapRow({ cap, onCopy }: { cap: MeetingCapability; onCopy: (text: string) => void }): JSX.Element {
  return (
    <li className={cap.ok ? 'ok' : 'bad'}>
      {cap.ok ? <Check size={13} /> : <AlertTriangle size={13} />}
      <div>
        <b>{cap.label}</b>
        <p>{cap.detail}</p>
        {!cap.ok && cap.fix && (
          <p className="act-fix">
            {cap.fix}
            <button className="icon-btn ghost xs" title="Copy the fix" onClick={() => onCopy(cap.fix)}><Copy size={11} /></button>
          </p>
        )}
      </div>
    </li>
  )
}

export default function MeetingSettings({ variant = 'page' }: { variant?: 'page' | 'modal' }): JSX.Element {
  const meetingStatus = useStore((s) => s.meetingStatus)
  const meetingPreflight = useStore((s) => s.meetingPreflight)
  const meetingBusy = useStore((s) => s.meetingBusy)
  const setMeetingConfig = useStore((s) => s.setMeetingConfig)
  const loadMeetingPreflight = useStore((s) => s.loadMeetingPreflight)
  const deleteMeetingAudio = useStore((s) => s.deleteMeetingAudio)
  const startRecording = useStore((s) => s.startRecording)
  const toast = useStore((s) => s.toast)
  const [test, setTest] = useState<{ state: 'idle' | 'running' | 'ok' | 'fail'; msg?: string }>({ state: 'idle' })

  useEffect(() => { void loadMeetingPreflight() }, [loadMeetingPreflight])

  const cfg = meetingStatus?.config
  if (!meetingStatus || !cfg) {
    return <div className="mtg-settings"><p className="muted">Loading the meeting recorder…</p></div>
  }

  // `start` is refused outright while the master switch is off (meetings.py inserts an `enabled`
  // blocker at index 0), so the button says why instead of offering a click that 409s.
  const recorderOff = !cfg.enabled
  const patch = (p: Partial<MeetingConfig>): void => void setMeetingConfig(p)
  const copy = (text: string): void => {
    void navigator.clipboard.writeText(text)
    toast('Copied')
  }
  const toggleSource = (s: string): void =>
    patch({ sources: cfg.sources.includes(s) ? cfg.sources.filter((x) => x !== s) : [...cfg.sources, s] })

  // The index is only meaningful next to the name it had when it was chosen: avfoundation
  // renumbers its inputs whenever a device is plugged in, and recording the wrong one silently is
  // worse than refusing to record.
  const deviceName = (index: string): string => meetingStatus.devices.find((d) => d.index === index)?.name ?? ''
  const pickDevice = (p: { micDevice?: string; outputDevice?: string }): void => {
    const next: Partial<MeetingConfig> = { ...p }
    if (p.micDevice !== undefined) next.micDeviceName = deviceName(p.micDevice)
    if (p.outputDevice !== undefined) next.outputDeviceName = deviceName(p.outputDevice)
    patch(next)
  }

  const runSelftest = async (): Promise<void> => {
    setTest({ state: 'running' })
    try {
      const r = await api.meetings.selftest()
      const t = r.selftest
      setTest(t.ok
        ? { state: 'ok', msg: `${t.backend}: recorded in ${t.record_ms}ms, transcribed in ${t.transcribe_ms}ms${t.text ? ` → “${t.text}”` : ''}` }
        : { state: 'fail', msg: t.error || 'The round trip failed and said nothing about why.' })
    } catch (e) {
      setTest({ state: 'fail', msg: (e as Error).message })
    }
  }

  const caps = meetingPreflight?.capabilities ?? []
  const blockers = meetingPreflight?.blockers ?? []
  const hasLoopback = meetingStatus.devices.some((d) => d.loopback)
  const nativeSystem = (caps.find((c) => c.id === 'loopback')?.detail ?? '').includes('process tap')
  const nativeMic = (caps.find((c) => c.id === 'ffmpeg')?.detail ?? '').includes('AVAudioEngine')
  const hasSystemAudio = nativeSystem || hasLoopback

  return (
    <div className="mtg-settings">
      {variant === 'page' && (
        <div className="mtg-intro">
          <h2><Mic size={18} /> Take notes on a meeting</h2>
          <p className="muted">
            Type whatever matters while the call runs. The recorder captures the audio in short
            clips, transcribes each one, and afterwards proposes a tidied-up version of your notes as
            a diff you accept or reject. What you typed is never overwritten.
          </p>
          <p className="muted small">
            Recording is off until you turn it on, nothing starts on its own, and no tool the
            assistant can call is able to start, stop or pause it.
          </p>
          <div className="add-row">
            <button className="primary-btn" disabled={meetingBusy || recorderOff}
                    title={recorderOff ? 'The meeting recorder is off. Turn it on below.' : undefined}
                    onClick={() => void startRecording()}>
              <Mic size={14} /> Record a meeting
            </button>
            {blockers.length > 0 && (
              <span className="act-pill warn">{blockers.length} blocker{blockers.length === 1 ? '' : 's'}</span>
            )}
          </div>
        </div>
      )}

      <h4 className="act-h">What this machine can do</h4>
      <p className="muted small">
        A row that is not ok blocks Start rather than degrading quietly. {!hasSystemAudio && (
          'System audio is unavailable, so only your own microphone can be captured — the far end of a call will not be in the transcript.'
        )}
      </p>
      <section className="act-card">
        <ul className="act-caps">
          {caps.length === 0 && <li className="bad"><AlertTriangle size={13} /><div><b>Not checked yet</b><p>Run the check to see what is available.</p></div></li>}
          {caps.map((c) => <CapRow key={c.id} cap={c} onCopy={copy} />)}
        </ul>
      </section>
      <div className="mtg-selftest">
        <button className="ghost-btn" disabled={meetingBusy} onClick={() => void loadMeetingPreflight(true)}>
          <RefreshCw size={13} className={meetingBusy ? 'spin' : ''} /> Re-check
        </button>
        <button className="ghost-btn" disabled={test.state === 'running'} onClick={() => void runSelftest()}>
          <Zap size={13} /> {test.state === 'running' ? 'Testing…' : 'Test transcription'}
        </button>
        {test.msg && <span className={`mtg-selftest-msg ${test.state}`}>{test.msg}</span>}
      </div>

      <h4 className="act-h">Recording</h4>
      <label className="toggle-row plain">
        <span className="toggle-icon"><Mic size={15} /></span>
        <span className="toggle-text">
          <b>Meeting recorder</b>
          <small>Off means no capture at all. Notes still work; the audio and transcript parts stay dark.</small>
        </span>
        <input type="checkbox" checked={cfg.enabled} onChange={(e) => patch({ enabled: e.target.checked })} />
        <span className="switch" />
      </label>
      <div className="mtg-sources">
        <label>
          <input type="checkbox" checked={cfg.sources.includes('mic')} onChange={() => toggleSource('mic')} />
          Capture my microphone
        </label>
        <label>
          <input type="checkbox" checked={cfg.sources.includes('output')} onChange={() => toggleSource('output')} disabled={!hasSystemAudio} />
          Capture system audio{!hasSystemAudio && ' (needs macOS 14.2+ or a loopback device)'}
        </label>
      </div>
      <AudioDevicePicker
        devices={meetingStatus.devices} micValue={cfg.micDevice} outputValue={cfg.outputDevice}
        nativeMic={nativeMic} nativeSystem={nativeSystem}
        onChange={pickDevice}
      />
      <NumberField label="Clip length" hint="How far behind live the transcript runs; longer clips transcribe better"
        value={cfg.segmentSeconds} min={5} max={120} step={5} suffix="seconds"
        onCommit={(v) => patch({ segmentSeconds: v })} />
      <NumberField label="Hard cap per meeting" hint="Stops capture even if the app is killed mid-call"
        value={cfg.maxMeetingSeconds} min={300} max={28800} step={300} suffix="seconds"
        onCommit={(v) => patch({ maxMeetingSeconds: v })} />
      <NumberField label="Drain on stop" hint="How long Stop waits for the transcription queue to finish"
        value={cfg.drainSeconds} min={0} max={600} step={10} suffix="seconds"
        onCommit={(v) => patch({ drainSeconds: v })} />
      <NumberField label="Auto-stop after the scheduled end" hint="Purely time-based; nothing here listens for silence"
        value={cfg.autoStopGraceSeconds} min={0} max={3600} step={60} suffix="seconds"
        onCommit={(v) => patch({ autoStopGraceSeconds: v })} />

      <h4 className="act-h">Transcription</h4>
      <label className="act-field">
        <span><b>Backend</b><small>“off” blocks Start rather than recording audio nothing will read</small></span>
        <select value={cfg.sttBackend} onChange={(e) => patch({ sttBackend: e.target.value as MeetingConfig['sttBackend'] })}>
          <option value="auto">Auto — Speech, then whisper.cpp, then proxy</option>
          <option value="speech">On-device Speech</option>
          <option value="proxy">Proxy only</option>
          <option value="local">Local whisper.cpp only</option>
          <option value="off">Off</option>
        </select>
      </label>
      <label className="act-field">
        <span><b>Speech-to-text model</b><small>on your configured base URL, which must route /v1/audio/transcriptions</small></span>
        <input value={cfg.sttModel} onChange={(e) => patch({ sttModel: e.target.value })} spellCheck={false} />
      </label>
      <label className="act-field">
        <span><b>whisper.cpp model file</b><small>a ggml .bin, for the local backend</small></span>
        <input value={cfg.whisperModelPath} onChange={(e) => patch({ whisperModelPath: e.target.value })} spellCheck={false} placeholder="/opt/models/ggml-base.en.bin" />
      </label>

      <h4 className="act-h">Notes</h4>
      <label className="act-field">
        <span><b>Default template</b><small>shapes the notes skeleton and the enhance prompt</small></span>
        <select value={cfg.template} onChange={(e) => patch({ template: e.target.value as MeetingTemplate })}>
          {(Object.keys(TEMPLATE_LABEL) as MeetingTemplate[]).map((t) => <option key={t} value={t}>{TEMPLATE_LABEL[t]}</option>)}
        </select>
      </label>
      <label className="toggle-row plain">
        <span className="toggle-icon"><Sparkles size={15} /></span>
        <span className="toggle-text">
          <b>Propose enhanced notes on stop</b>
          <small>Writes a proposal you accept or reject. It cannot touch what you typed.</small>
        </span>
        <input type="checkbox" checked={cfg.enhanceOnStop} onChange={(e) => patch({ enhanceOnStop: e.target.checked })} />
        <span className="switch" />
      </label>
      <label className="act-field">
        <span><b>Enhance model</b><small>blank falls back to the extraction model, then the default chat model</small></span>
        <input value={cfg.enhanceModel} onChange={(e) => patch({ enhanceModel: e.target.value })} spellCheck={false} placeholder="(default)" />
      </label>
      <NumberField label="Transcript sent to the model" hint="Capped head and tail — decisions land at the end of a call"
        value={cfg.maxTranscriptChars} min={2000} max={400000} step={1000} suffix="characters"
        onCommit={(v) => patch({ maxTranscriptChars: v })} />

      <h4 className="act-h">Calendar</h4>
      <label className="toggle-row plain">
        <span className="toggle-icon"><FileText size={15} /></span>
        <span className="toggle-text">
          <b>Start recording when a meeting begins</b>
          <small>Off only offers. On starts capture by itself, which records whoever is in the room.</small>
        </span>
        <input type="checkbox" checked={cfg.autoRecord} onChange={(e) => patch({ autoRecord: e.target.checked })} />
        <span className="switch" />
      </label>
      <NumberField label="Offer this early" hint="How long before an event it shows up as something to take notes on"
        value={cfg.nudgeSeconds} min={0} max={3600} step={60} suffix="seconds"
        onCommit={(v) => patch({ nudgeSeconds: v })} />
      <NumberField label="Ignore events smaller than" hint="A solo block on your calendar is not a meeting"
        value={cfg.minAttendees} min={0} max={20} suffix="attendees"
        onCommit={(v) => patch({ minAttendees: v })} />

      <h4 className="act-h">Privacy and disk</h4>
      <label className="toggle-row plain">
        <span className="toggle-icon"><Shield size={15} /></span>
        <span className="toggle-text">
          <b>Scrub credentials before storing</b>
          <small>Strips API keys, card numbers and anything following a word like “password”. Emails and phone numbers are left alone on purpose — redacting them out of a transcript destroys who said what.</small>
        </span>
        <input type="checkbox" checked={cfg.redactSecrets} onChange={(e) => patch({ redactSecrets: e.target.checked })} />
        <span className="switch" />
      </label>
      <label className="toggle-row plain">
        <span className="toggle-icon"><Sparkles size={15} /></span>
        <span className="toggle-text">
          <b>Feed recent meetings into chats</b>
          <small>Off keeps recording and enhancing, but no chat sees any of it. Individual chats can opt out separately.</small>
        </span>
        <input type="checkbox" checked={cfg.injectContext} onChange={(e) => patch({ injectContext: e.target.checked })} />
        <span className="switch" />
      </label>
      <label className="toggle-row plain">
        <span className="toggle-icon"><Mic size={15} /></span>
        <span className="toggle-text">
          <b>Show dictation as you speak</b>
          <small>Words appear in a small pill at the cursor while you talk, then the settled text is typed in. Uses on-device Speech Recognition and needs its permission.</small>
        </span>
        <input type="checkbox" checked={cfg.livePreview} onChange={(e) => patch({ livePreview: e.target.checked })} />
        <span className="switch" />
      </label>
      <label className="toggle-row plain">
        <span className="toggle-icon"><Mic size={15} /></span>
        <span className="toggle-text">
          <b>Keep the audio after transcribing</b>
          <small>Off deletes each wav once its text has landed. On keeps them, so you can re-transcribe later with a better model.</small>
        </span>
        <input type="checkbox" checked={cfg.keepAudio} onChange={(e) => patch({ keepAudio: e.target.checked })} />
        <span className="switch" />
      </label>
      <NumberField label="Audio kept on disk at most" hint="Oldest failed clip is evicted first once this is reached"
        value={Math.round(cfg.maxAudioBytes / MIB)} min={64} max={65536} step={64} suffix="MiB"
        onCommit={(v) => patch({ maxAudioBytes: v * MIB })} />

      <div className="mtg-danger">
        <span className="mtg-danger-body">
          <b>Delete all meeting audio</b>
          <small>Every retained wav, for every meeting. The notes, transcripts and enhanced notes stay.</small>
        </span>
        <button className="ghost-btn danger" disabled={meetingBusy}
          onClick={() => { if (confirm('Delete every recorded wav? The transcripts and notes are kept.')) void deleteMeetingAudio() }}>
          <Trash2 size={14} /> Delete audio
        </button>
      </div>
    </div>
  )
}
