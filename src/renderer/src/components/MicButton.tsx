import { useEffect, useRef, useState, type RefObject } from 'react'
import { Loader2, Mic, Square } from 'lucide-react'
import { api } from '../lib/api'
import { ApiError } from '../lib/apiError'
import { startWavRecording, type WavRecording } from '../lib/wavRecorder'
import { useStore } from '../store'
import { chordDown, chordFsm, chordUp, DEFAULT_CHORD, initialChord, parseChord, type ChordState } from '../features/docrec/chord'
import { dictationCommand } from '../features/docrec/dictation'

/** The backend refuses a longer clip (2 minutes); stop just short of it. */
const MAX_MS = 119_000

type Phase = 'idle' | 'starting' | 'recording' | 'transcribing'

const clock = (ms: number): string => { const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` }

/**
 * Dictation into the chat composer. Click to start and again to stop, or hold the dictation chord
 * (tap it to latch) while the composer has focus; Esc drops the clip. The transcript is handed to
 * `onText` for insertion at the caret and is never sent.
 */
export default function MicButton({ scope, onText }: { scope: RefObject<HTMLElement>; onText: (raw: string) => void }): JSX.Element {
  const [phase, setPhase] = useState<Phase>('idle')
  const [elapsed, setElapsed] = useState(0)
  const rec = useRef<WavRecording | null>(null)
  const startedAt = useRef(0)
  const phaseRef = useRef(phase)
  phaseRef.current = phase
  const onTextRef = useRef(onText)
  onTextRef.current = onText
  const spec = useStore((s) => s.settings.dictationChord) || DEFAULT_CHORD

  const start = async (): Promise<void> => {
    if (phaseRef.current !== 'idle') return
    setPhase('starting')
    phaseRef.current = 'starting'
    const toast = useStore.getState().toast
    try {
      const access = (await window.os?.micAccess?.()) ?? 'granted'
      if (access !== 'granted') {
        toast('Grain has no microphone access.', 'error', { label: 'Grant in System Settings', run: () => void api.activity.openPermissionSettings('microphone') })
        setPhase('idle')
        return
      }
      rec.current = await startWavRecording()
      startedAt.current = Date.now()
      setElapsed(0)
      setPhase('recording')
    } catch (e) {
      toast(`Could not open the microphone: ${(e as Error).message}`, 'error')
      setPhase('idle')
    }
  }

  const cancel = (): void => {
    rec.current?.cancel()
    rec.current = null
    setPhase('idle')
  }

  const stop = async (): Promise<void> => {
    const r = rec.current
    if (!r || phaseRef.current !== 'recording') return
    rec.current = null
    setPhase('transcribing')
    phaseRef.current = 'transcribing'
    const toast = useStore.getState().toast
    try {
      const res = await api.assist.transcribe(await r.stop())
      if (res.error) toast(`Transcription failed: ${res.error}`, 'error')
      else if (res.text) {
        // The same optional tidy-up pass as doc dictation; the server returns the text as is when it is off.
        const text = dictationCommand(res.text) ? res.text : await api.assist.cleanDictation(res.text).then((c) => c.text || res.text, () => res.text)
        onTextRef.current(text)
      }
    } catch (e) {
      const fixable = e instanceof ApiError && e.status === 409
      toast((e as Error).message, 'error', fixable ? { label: 'Open settings', run: () => useStore.getState().openSettings('meetings') } : undefined)
    } finally {
      setPhase('idle')
    }
  }

  const live = useRef({ start, stop, cancel })
  live.current = { start, stop, cancel }

  // The elapsed pill, and the hard stop before the clip outgrows what the backend accepts.
  useEffect(() => {
    if (phase !== 'recording') return
    const t = setInterval(() => {
      const ms = Date.now() - startedAt.current
      setElapsed(ms)
      if (ms >= MAX_MS) void live.current.stop()
    }, 250)
    return () => clearInterval(t)
  }, [phase])

  // A recording never outlives the composer.
  useEffect(() => () => rec.current?.cancel(), [])

  // Listened for on the composer itself, not the window, and stopped there: the Docs view's
  // window-level chord would otherwise start a doc dictation from the same keys.
  useEffect(() => {
    const el = scope.current
    const chord = parseChord(spec)
    if (!el) return
    let st: ChordState = initialChord
    const feed = (ev: 'down' | 'up'): boolean => {
      if (st.latched && phaseRef.current !== 'recording') st = initialChord
      const [next, action] = chordFsm(st, ev, Date.now(), phaseRef.current === 'idle')
      st = next
      if (action === 'start') void live.current.start()
      else if (action === 'stop') void live.current.stop()
      return action !== null || next.held
    }
    const down = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && phaseRef.current === 'recording') { e.preventDefault(); e.stopPropagation(); live.current.cancel(); return }
      if (chord && chordDown(e, chord)) { e.stopPropagation(); if (feed('down')) e.preventDefault() }
    }
    const up = (e: KeyboardEvent): void => { if (chord && chordUp(e, chord)) { e.stopPropagation(); feed('up') } }
    el.addEventListener('keydown', down)
    el.addEventListener('keyup', up)
    return () => { el.removeEventListener('keydown', down); el.removeEventListener('keyup', up) }
  }, [scope, spec])

  const recording = phase === 'recording'
  return (
    <>
      {recording && <span className="mic-pill" role="status" title="Recording. Esc discards it.">{clock(elapsed)}</span>}
      <button className={recording ? 'icon-btn mic-btn recording' : 'icon-btn mic-btn'} type="button"
        disabled={phase === 'starting' || phase === 'transcribing'}
        aria-pressed={recording}
        aria-label={recording ? 'Stop dictation' : phase === 'transcribing' ? 'Transcribing' : 'Dictate'}
        title={recording ? 'Stop and insert the text (Esc discards)' : `Dictate into the message. Hold ${spec} to talk; tap it to latch`}
        onMouseDown={(e) => e.preventDefault() /* keep the caret where it is */}
        onClick={() => void (recording ? stop() : start())}>
        {phase === 'transcribing' || phase === 'starting' ? <Loader2 size={16} className="spin" /> : recording ? <Square size={14} /> : <Mic size={16} />}
      </button>
    </>
  )
}
